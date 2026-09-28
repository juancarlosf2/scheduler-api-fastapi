"""Hosts scheduling operations."""

from __future__ import annotations
from app.features.hosts.models import Host
from app.features.availability.models import Schedule
from app.features.hosts.schemas import HostCreate
from app.core.errors import DomainError


class HostMixin:
    def get_host(self, host_id: str) -> Host:
        host = self.session.get(Host, host_id)
        if host is None:
            raise DomainError("Host not found", 404)
        return host

    def create_host(self, data: HostCreate) -> Host:
        host = Host(
            username=data.username,
            display_name=data.display_name.strip(),
            email=data.email.strip().lower(),
            timezone=data.timezone,
            api_key_hash=data.api_key_hash,
        )
        schedule = Schedule(host=host, name="Default hours", timezone=data.timezone)
        self.session.add(schedule)
        self._commit()
        return host
