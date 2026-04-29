"""User Answer Machine — drafts brand-aligned replies to incoming comments/DMs.

Public API:
- load_knowledge_base(force_rebuild=False) -> KnowledgeBase
- draft_reply(message, *, client, model, kb=None, platform_hint=None) -> dict
"""
from .kb import KnowledgeBase, append_exemplar, load_knowledge_base
from .agent import draft_reply

__all__ = ["KnowledgeBase", "append_exemplar", "draft_reply", "load_knowledge_base"]
