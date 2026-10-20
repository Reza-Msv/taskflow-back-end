"""Centralized database models registry.

Importing this module ensures that all SQLAlchemy ORM models across all domains
are imported and registered with Base.metadata and Base.registry. This allows
cross-domain string relationship targets (e.g., 'Sprint', 'WorkItemComment')
to be resolved cleanly during configure_mappers().
"""

import taskflow.domains.collaboration.models  # noqa: F401
import taskflow.domains.identity.models  # noqa: F401
import taskflow.domains.organizations.models  # noqa: F401
import taskflow.domains.projects.models  # noqa: F401
import taskflow.domains.scrum.models  # noqa: F401
import taskflow.domains.work_items.models  # noqa: F401
