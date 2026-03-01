"""Command parser for file references."""

import re
from typing import List, Optional, Tuple
from dataclasses import dataclass

from .models import SessionFileMeta


@dataclass
class FileReference:
    """Parsed file reference."""
    type: str  # file, last, all, chunk
    value: Optional[str] = None  # file_id or chunk_id


class CommandParser:
    """Parse file reference commands."""
    
    PATTERN = r'@(file_[a-zA-Z0-9]+|last|all|chunk_[a-zA-Z0-9]+)'
    
    @classmethod
    def parse(cls, text: str) -> Tuple[str, List[FileReference]]:
        """Parse command references from text.
        
        Returns:
            (cleaned_message, list of references)
        """
        references = []
        
        for match in re.finditer(cls.PATTERN, text):
            ref_text = match.group(1)
            
            if ref_text.startswith('file_'):
                ref = FileReference(type='file', value=ref_text)
            elif ref_text == 'last':
                ref = FileReference(type='last')
            elif ref_text == 'all':
                ref = FileReference(type='all')
            elif ref_text.startswith('chunk_'):
                ref = FileReference(type='chunk', value=ref_text)
            else:
                continue
            
            references.append(ref)
        
        # Remove command syntax from message
        cleaned = re.sub(cls.PATTERN, '', text).strip()
        
        return cleaned, references
    
    @classmethod
    def resolve_references(
        cls,
        references: List[FileReference],
        session_files: List[SessionFileMeta]
    ) -> List[str]:
        """Resolve references to file_ids.
        
        Priority: chunk > file > last > all
        """
        # If there are chunk references, we can't resolve to files directly
        # The retrieval will handle chunk-specific lookup
        
        has_chunk_refs = any(r.type == 'chunk' for r in references)
        if has_chunk_refs:
            # Return empty - chunk IDs need direct lookup
            # The caller should handle chunk-specific retrieval
            return []
        
        file_ids = set()
        
        # Process by priority order
        ref_by_type = {
            'chunk': [],
            'file': [],
            'last': [],
            'all': []
        }
        
        for ref in references:
            ref_by_type[ref.type].append(ref)
        
        # File references (highest explicit priority)
        for ref in ref_by_type['file']:
            file_ids.add(ref.value)
        
        # Last reference
        if ref_by_type['last'] and session_files:
            # Get the most recently uploaded file
            last_file = session_files[-1]
            file_ids.add(last_file.file_id)
        
        # All references (lowest priority - only if no other refs)
        if ref_by_type['all'] and not file_ids:
            file_ids.update(f.file_id for f in session_files)
        
        return list(file_ids)
    
    @classmethod
    def extract_chunk_refs(cls, references: List[FileReference]) -> List[str]:
        """Extract chunk IDs from references."""
        return [r.value for r in references if r.type == 'chunk']


# Export for convenience
parser = CommandParser()
