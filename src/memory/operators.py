"""Memory operators for consolidation workflow.

This module provides a minimal contract for memory operations.
Reserved for future: hooking LLM to output ops.

Note: This is scaffolding only - not yet integrated into agent flow.
"""

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from enum import Enum


class MemoryOpType(str, Enum):
    """Types of memory operations."""
    ADD = "ADD"
    UPDATE = "UPDATE"
    MERGE = "MERGE"
    DELETE = "DELETE"
    NOOP = "NOOP"


@dataclass
class MemoryOp:
    """Represents a memory operation.
    
    Attributes:
        op: Operation type (ADD, UPDATE, MERGE, DELETE, NOOP)
        target_id: Target entry ID (None for ADD)
        payload: Operation payload (content, metadata, etc.)
        reason: Reason for the operation (for logging/audit)
    """
    op: MemoryOpType
    target_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    
    def __repr__(self) -> str:
        return f"MemoryOp(op={self.op.value}, target={self.target_id}, reason={self.reason})"


def apply_ops(memory_backend, ops: List[MemoryOp]) -> List[str]:
    """Apply memory operations to a memory backend.
    
    Args:
        memory_backend: Memory backend (e.g., LightweightMemory)
        ops: List of operations to apply
        
    Returns:
        List of operation results (success/error messages)
    """
    results = []
    
    for mem_op in ops:
        try:
            if mem_op.op == MemoryOpType.ADD:
                # ADD: Create new entry
                if not mem_op.payload or 'content' not in mem_op.payload:
                    results.append(f"ADD failed: no content provided")
                    continue
                
                # Use stable ID: either provided or SHA256 hash of content
                if mem_op.payload.get('id'):
                    entry_id = mem_op.payload['id']
                else:
                    # Use SHA256 for stable, deterministic ID
                    content_hash = hashlib.sha256(mem_op.payload['content'].encode()).hexdigest()[:16]
                    entry_id = f"mem:{content_hash}"
                
                content = mem_op.payload['content']
                metadata = mem_op.payload.get('metadata', {})
                
                # Use upsert to add
                if hasattr(memory_backend, 'upsert'):
                    memory_backend.upsert(
                        entry_id=entry_id,
                        content=content,
                        metadata=metadata,
                    )
                    results.append(f"ADD: Created entry {entry_id}")
                else:
                    results.append(f"ADD failed: backend doesn't support upsert")
                    
            elif mem_op.op == MemoryOpType.UPDATE:
                # UPDATE: Replace existing entry
                if not mem_op.target_id:
                    results.append(f"UPDATE failed: no target_id")
                    continue
                
                if not mem_op.payload or 'content' not in mem_op.payload:
                    results.append(f"UPDATE failed: no content provided")
                    continue
                
                content = mem_op.payload['content']
                metadata = mem_op.payload.get('metadata', {})
                
                if hasattr(memory_backend, 'upsert'):
                    memory_backend.upsert(
                        entry_id=mem_op.target_id,
                        content=content,
                        metadata=metadata,
                    )
                    results.append(f"UPDATE: Updated entry {mem_op.target_id}")
                else:
                    results.append(f"UPDATE failed: backend doesn't support upsert")
                    
            elif mem_op.op == MemoryOpType.DELETE:
                # DELETE: Remove entry
                if not mem_op.target_id:
                    results.append(f"DELETE failed: no target_id")
                    continue
                
                if hasattr(memory_backend, 'delete'):
                    success = memory_backend.delete(mem_op.target_id)
                    if success:
                        results.append(f"DELETE: Removed entry {mem_op.target_id}")
                    else:
                        results.append(f"DELETE: Entry {mem_op.target_id} not found")
                else:
                    results.append(f"DELETE failed: backend doesn't support delete")
                    
            elif mem_op.op == MemoryOpType.MERGE:
                # MERGE: Append to existing entry
                if not mem_op.target_id:
                    results.append(f"MERGE failed: no target_id")
                    continue
                
                if not mem_op.payload or 'content' not in mem_op.payload:
                    results.append(f"MERGE failed: no content provided")
                    continue
                
                # Get existing, append new content
                if hasattr(memory_backend, 'get_entry'):
                    existing = memory_backend.get_entry(mem_op.target_id)
                    if existing:
                        new_content = existing['content'] + "\n\n" + mem_op.payload['content']
                        metadata = mem_op.payload.get('metadata', existing.get('meta', {}))
                        
                        if hasattr(memory_backend, 'upsert'):
                            memory_backend.upsert(
                                entry_id=mem_op.target_id,
                                content=new_content,
                                metadata=metadata,
                            )
                            results.append(f"MERGE: Appended to entry {mem_op.target_id}")
                    else:
                        results.append(f"MERGE: Entry {mem_op.target_id} not found")
                else:
                    results.append(f"MERGE failed: backend doesn't support get_entry")
                    
            elif mem_op.op == MemoryOpType.NOOP:
                # NOOP: No operation
                results.append(f"NOOP: {mem_op.reason or 'no action'}")
                
            else:
                results.append(f"Unknown operation: {mem_op.op}")
                
        except Exception as e:
            results.append(f"Error applying {mem_op.op.value}: {str(e)}")
    
    return results


def create_add_op(content: str, metadata: Dict = None, reason: str = None) -> MemoryOp:
    """Create an ADD operation.
    
    Args:
        content: Content to add
        metadata: Optional metadata
        reason: Optional reason
        
    Returns:
        MemoryOp for ADD
    """
    return MemoryOp(
        op=MemoryOpType.ADD,
        payload={'content': content, 'metadata': metadata or {}},
        reason=reason,
    )


def create_update_op(target_id: str, content: str, metadata: Dict = None, reason: str = None) -> MemoryOp:
    """Create an UPDATE operation.
    
    Args:
        target_id: Entry ID to update
        content: New content
        metadata: Optional metadata
        reason: Optional reason
        
    Returns:
        MemoryOp for UPDATE
    """
    return MemoryOp(
        op=MemoryOpType.UPDATE,
        target_id=target_id,
        payload={'content': content, 'metadata': metadata or {}},
        reason=reason,
    )


def create_delete_op(target_id: str, reason: str = None) -> MemoryOp:
    """Create a DELETE operation.
    
    Args:
        target_id: Entry ID to delete
        reason: Optional reason
        
    Returns:
        MemoryOp for DELETE
    """
    return MemoryOp(
        op=MemoryOpType.DELETE,
        target_id=target_id,
        reason=reason,
    )


def create_merge_op(target_id: str, content: str, reason: str = None) -> MemoryOp:
    """Create a MERGE operation.
    
    Args:
        target_id: Entry ID to merge into
        content: Content to append
        reason: Optional reason
        
    Returns:
        MemoryOp for MERGE
    """
    return MemoryOp(
        op=MemoryOpType.MERGE,
        target_id=target_id,
        payload={'content': content},
        reason=reason,
    )


def create_noop_op(reason: str = None) -> MemoryOp:
    """Create a NOOP operation.
    
    Args:
        reason: Reason for no operation
        
    Returns:
        MemoryOp for NOOP
    """
    return MemoryOp(
        op=MemoryOpType.NOOP,
        reason=reason,
    )
