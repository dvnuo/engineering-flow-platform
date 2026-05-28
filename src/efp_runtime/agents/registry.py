"""Agent profile registry for Runtime v2."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .profile import AgentProfile


class AgentRegistry:
    """In-memory registry keyed by agent profile name."""

    def __init__(
        self,
        profiles: Iterable[AgentProfile] | None = None,
        *,
        default_agent: str | None = "general",
    ):
        self._profiles: dict[str, AgentProfile] = {}
        self.default_agent = _normalize_optional_name(default_agent)
        for profile in profiles or []:
            self.register(profile)

    def register(self, profile: AgentProfile) -> AgentProfile:
        if not isinstance(profile, AgentProfile):
            raise TypeError("profile must be an AgentProfile")
        if profile.name in self._profiles:
            raise ValueError(f"Agent profile already registered: {profile.name}")
        self._profiles[profile.name] = profile
        return profile

    def get(self, name: str | None) -> AgentProfile | None:
        normalized = _normalize_optional_name(name)
        if normalized is None:
            return None
        return self._profiles.get(normalized)

    def resolve(self, name: str | None) -> AgentProfile:
        requested = _normalize_optional_name(name)
        if requested is not None and requested in self._profiles:
            return self._profiles[requested]

        if self.default_agent is not None and self.default_agent in self._profiles:
            return self._profiles[self.default_agent]

        available = ", ".join(self.names()) or "<none>"
        raise KeyError(
            f"Unknown agent profile: {requested or '<empty>'}. "
            f"Available agents: {available}"
        )

    def names(self) -> list[str]:
        return sorted(self._profiles)

    def profiles(self) -> list[AgentProfile]:
        return [self._profiles[name] for name in self.names()]

    @classmethod
    def from_mappings(
        cls,
        profiles: Mapping[str, Mapping[str, Any] | AgentProfile]
        | Iterable[Mapping[str, Any] | AgentProfile],
        *,
        default_agent: str | None = "general",
    ) -> "AgentRegistry":
        if isinstance(profiles, Mapping):
            resolved = [
                _profile_from_named_mapping(name, payload)
                for name, payload in profiles.items()
            ]
        else:
            resolved = [_coerce_profile(payload) for payload in profiles]
        return cls(resolved, default_agent=default_agent)


def _coerce_profile(payload: Mapping[str, Any] | AgentProfile) -> AgentProfile:
    if isinstance(payload, AgentProfile):
        return payload
    if isinstance(payload, Mapping):
        if "name" not in payload:
            raise ValueError("profile mapping requires a name")
        return AgentProfile(**dict(payload))
    raise TypeError("profile payload must be an AgentProfile or mapping")


def _profile_from_named_mapping(
    name: str,
    payload: Mapping[str, Any] | AgentProfile,
) -> AgentProfile:
    if isinstance(payload, AgentProfile):
        return payload
    if not isinstance(payload, Mapping):
        raise TypeError("profile mapping value must be an AgentProfile or mapping")
    data = dict(payload)
    data.setdefault("name", name)
    return AgentProfile(**data)


def _normalize_optional_name(name: str | None) -> str | None:
    if name is None:
        return None
    normalized = str(name).strip()
    return normalized or None


__all__ = ["AgentRegistry"]
