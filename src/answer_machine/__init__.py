"""User Answer Machine — drafts brand-aligned replies to incoming comments/DMs.

Public API:
- load_knowledge_base(force_rebuild=False) -> KnowledgeBase
- draft_reply(message, *, client, model, kb=None, platform_hint=None) -> dict
"""
from .kb import KnowledgeBase, load_knowledge_base
from .agent import draft_reply

__all__ = ["KnowledgeBase", "load_knowledge_base", "draft_reply"]
