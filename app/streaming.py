"""Real-time streaming support for bisect job output."""

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional, Callable
from datetime import datetime

logger = logging.getLogger(__name__)

# Configure detailed logging for streaming operations
STREAM_DEBUG = True  # Enable verbose streaming logs


@dataclass
class StreamMessage:
    """A single message in the stream."""
    type: str  # 'log', 'status', 'progress', 'result'
    content: str
    timestamp: float = field(default_factory=time.time)
    
    def to_sse(self) -> str:
        """Convert to Server-Sent Events format."""
        return f"event: {self.type}\ndata: {self.content}\n\n"


class JobStreamManager:
    """Manages streaming output for bisect jobs."""
    
    def __init__(self, max_buffer_size: int = 1000):
        # job_id -> list of messages
        self._buffers: dict[int, list[StreamMessage]] = defaultdict(list)
        # job_id -> set of asyncio.Event for subscribers
        self._subscribers: dict[int, set[asyncio.Event]] = defaultdict(set)
        # job_id -> whether job is complete
        self._completed: dict[int, bool] = {}
        self._max_buffer_size = max_buffer_size
        self._lock = asyncio.Lock()
    
    async def publish(self, job_id: int, message: StreamMessage) -> None:
        """Publish a message to all subscribers of a job."""
        if STREAM_DEBUG and message.type != "keepalive":
            logger.debug(f"[Stream] job={job_id} type={message.type} len={len(message.content)}")
        
        async with self._lock:
            # Add to buffer
            buffer = self._buffers[job_id]
            buffer.append(message)
            
            # Trim buffer if too large
            if len(buffer) > self._max_buffer_size:
                self._buffers[job_id] = buffer[-self._max_buffer_size:]
                if STREAM_DEBUG:
                    logger.debug(f"[Stream] job={job_id} buffer trimmed to {self._max_buffer_size}")
            
            # Notify all subscribers
            subscriber_count = len(self._subscribers[job_id])
            for event in self._subscribers[job_id]:
                event.set()
            
            if STREAM_DEBUG and subscriber_count > 0:
                logger.debug(f"[Stream] job={job_id} notified {subscriber_count} subscribers")
    
    async def publish_log(self, job_id: int, content: str) -> None:
        """Convenience method to publish a log message."""
        await self.publish(job_id, StreamMessage(type="log", content=content))
    
    async def publish_status(self, job_id: int, status: str) -> None:
        """Convenience method to publish a status update."""
        await self.publish(job_id, StreamMessage(type="status", content=status))
    
    async def publish_progress(self, job_id: int, step: int, total: int, message: str) -> None:
        """Convenience method to publish progress updates."""
        content = f"{step}/{total}|{message}"
        await self.publish(job_id, StreamMessage(type="progress", content=content))
    
    async def mark_complete(self, job_id: int) -> None:
        """Mark a job as complete and notify all subscribers."""
        logger.info(f"[Stream] job={job_id} marked as complete")
        async with self._lock:
            self._completed[job_id] = True
            # Notify all subscribers that the job is complete
            subscriber_count = len(self._subscribers[job_id])
            for event in self._subscribers[job_id]:
                event.set()
            if STREAM_DEBUG:
                logger.debug(f"[Stream] job={job_id} notified {subscriber_count} subscribers of completion")
    
    def is_complete(self, job_id: int) -> bool:
        """Check if a job is marked as complete."""
        return self._completed.get(job_id, False)
    
    async def subscribe(self, job_id: int, start_from: int = 0) -> AsyncGenerator[StreamMessage, None]:
        """Subscribe to a job's stream. Yields messages as they arrive."""
        event = asyncio.Event()
        subscribe_time = time.time()
        messages_sent = 0
        keepalives_sent = 0
        
        logger.info(f"[Stream] job={job_id} new subscriber starting from index {start_from}")
        
        async with self._lock:
            self._subscribers[job_id].add(event)
            buffer = self._buffers[job_id]
            current_index = start_from
            logger.debug(f"[Stream] job={job_id} current buffer size: {len(buffer)}")
        
        try:
            while True:
                # Get any new messages
                async with self._lock:
                    buffer = self._buffers[job_id]
                    new_messages = buffer[current_index:]
                    current_index = len(buffer)
                    is_complete = self._completed.get(job_id, False)
                
                # Yield new messages
                for msg in new_messages:
                    messages_sent += 1
                    yield msg
                
                if new_messages and STREAM_DEBUG:
                    logger.debug(f"[Stream] job={job_id} sent {len(new_messages)} messages (total={messages_sent})")
                
                # If job is complete and we've sent all messages, we're done
                if is_complete:
                    logger.info(f"[Stream] job={job_id} subscription complete: {messages_sent} messages sent")
                    break
                
                # Wait for new messages with timeout
                event.clear()
                try:
                    await asyncio.wait_for(event.wait(), timeout=30.0)
                except asyncio.TimeoutError:
                    # Send a keepalive and continue
                    keepalives_sent += 1
                    if STREAM_DEBUG:
                        elapsed = time.time() - subscribe_time
                        logger.debug(f"[Stream] job={job_id} keepalive #{keepalives_sent} (elapsed={elapsed:.0f}s)")
                    yield StreamMessage(type="keepalive", content="")
        finally:
            elapsed = time.time() - subscribe_time
            logger.info(
                f"[Stream] job={job_id} subscriber disconnected after {elapsed:.1f}s "
                f"(messages={messages_sent}, keepalives={keepalives_sent})"
            )
            async with self._lock:
                self._subscribers[job_id].discard(event)
    
    async def get_buffer(self, job_id: int) -> list[StreamMessage]:
        """Get all buffered messages for a job."""
        async with self._lock:
            return list(self._buffers.get(job_id, []))
    
    async def cleanup(self, job_id: int) -> None:
        """Clean up resources for a completed job after some time."""
        async with self._lock:
            self._buffers.pop(job_id, None)
            self._subscribers.pop(job_id, None)
            self._completed.pop(job_id, None)


# Global singleton for the stream manager
_stream_manager: Optional[JobStreamManager] = None


def get_stream_manager() -> JobStreamManager:
    """Get the global stream manager instance."""
    global _stream_manager
    if _stream_manager is None:
        _stream_manager = JobStreamManager()
    return _stream_manager


class SyncStreamPublisher:
    """Synchronous wrapper for publishing to the stream manager.
    
    Used by the bisect runner which runs in a thread pool.
    """
    
    def __init__(self, job_id: int, loop: asyncio.AbstractEventLoop):
        self.job_id = job_id
        self.loop = loop
        self.manager = get_stream_manager()
        self._log_count = 0
        self._start_time = time.time()
        logger.info(f"[SyncStream] job={job_id} publisher created")
    
    def _safe_publish(self, coro) -> None:
        """Safely publish to the async stream, handling errors gracefully."""
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self.loop)
            # Don't wait for result - fire and forget for performance
            # But add a callback to log errors
            def handle_error(fut):
                try:
                    fut.result()
                except Exception as e:
                    logger.warning(f"[SyncStream] job={self.job_id} publish error: {e}")
            future.add_done_callback(handle_error)
        except Exception as e:
            logger.warning(f"[SyncStream] job={self.job_id} failed to schedule publish: {e}")
    
    def publish_log(self, content: str) -> None:
        """Publish a log message from a sync context."""
        self._log_count += 1
        if STREAM_DEBUG and self._log_count % 50 == 0:
            elapsed = time.time() - self._start_time
            logger.debug(f"[SyncStream] job={self.job_id} published {self._log_count} logs in {elapsed:.1f}s")
        self._safe_publish(self.manager.publish_log(self.job_id, content))
    
    def publish_status(self, status: str) -> None:
        """Publish a status update from a sync context."""
        logger.info(f"[SyncStream] job={self.job_id} status: {status}")
        self._safe_publish(self.manager.publish_status(self.job_id, status))
    
    def publish_progress(self, step: int, total: int, message: str) -> None:
        """Publish a progress update from a sync context."""
        if STREAM_DEBUG:
            logger.debug(f"[SyncStream] job={self.job_id} progress: {step}/{total} - {message}")
        self._safe_publish(self.manager.publish_progress(self.job_id, step, total, message))
    
    def mark_complete(self) -> None:
        """Mark the job as complete from a sync context."""
        elapsed = time.time() - self._start_time
        logger.info(f"[SyncStream] job={self.job_id} complete after {elapsed:.1f}s ({self._log_count} logs)")
        self._safe_publish(self.manager.mark_complete(self.job_id))

