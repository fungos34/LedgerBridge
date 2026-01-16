"""
Reference Mapper for Firefly Sync Assistant.

Maps entity references from source pool records to target Firefly instance IDs.
When importing entities, references (like account names, category names) need to
be resolved to the actual IDs in the target user's Firefly instance.

SSOT: This module is the single source of truth for reference resolution during
sync imports.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..firefly_client.client import FireflyClient

logger = logging.getLogger(__name__)


class ReferenceMapper:
    """
    Maps source entity references to target Firefly IDs.

    Caches resolved references to minimize API calls during bulk imports.
    """

    def __init__(self, client: "FireflyClient"):
        """
        Initialize the reference mapper.

        Args:
            client: FireflyClient connected to the target Firefly instance
        """
        self.client = client
        self._cache: dict[str, int | None] = {}
        self._account_cache_loaded = False
        self._category_cache_loaded = False
        self._tag_cache_loaded = False
        self._budget_cache_loaded = False
        self._rule_group_cache_loaded = False

    def _load_accounts(self) -> None:
        """Pre-load all accounts into cache."""
        if self._account_cache_loaded:
            return

        for acc_type in ["asset", "expense", "revenue", "liability", "cash"]:
            accounts = self.client.list_accounts(acc_type)
            for acc in accounts:
                key = f"account:{acc_type}:{acc['name'].lower().strip()}"
                self._cache[key] = int(acc["id"])

        self._account_cache_loaded = True

    def _load_categories(self) -> None:
        """Pre-load all categories into cache."""
        if self._category_cache_loaded:
            return

        categories = self.client.list_categories()
        for cat in categories:
            key = f"category:{cat.name.lower().strip()}"
            self._cache[key] = cat.id

        self._category_cache_loaded = True

    def _load_tags(self) -> None:
        """Pre-load all tags into cache."""
        if self._tag_cache_loaded:
            return

        tags = self.client.list_tags()
        for tag in tags:
            key = f"tag:{tag['tag'].lower().strip()}"
            self._cache[key] = int(tag["id"])

        self._tag_cache_loaded = True

    def _load_budgets(self) -> None:
        """Pre-load all budgets into cache."""
        if self._budget_cache_loaded:
            return

        budgets = self.client.list_budgets()
        for budget in budgets:
            key = f"budget:{budget['name'].lower().strip()}"
            self._cache[key] = int(budget["id"])

        self._budget_cache_loaded = True

    def _load_rule_groups(self) -> None:
        """Pre-load all rule groups into cache."""
        if self._rule_group_cache_loaded:
            return

        rule_groups = self.client.list_rule_groups()
        for rg in rule_groups:
            key = f"rule_group:{rg['title'].lower().strip()}"
            self._cache[key] = int(rg["id"])

        self._rule_group_cache_loaded = True

    def resolve_account(
        self,
        name: str,
        account_type: str,
        create_if_missing: bool = True,
        currency_code: str = "EUR",
    ) -> int | None:
        """
        Resolve account name to Firefly ID.

        Args:
            name: Account name to resolve
            account_type: Account type (asset, expense, revenue, etc.)
            create_if_missing: If True, create the account if not found
            currency_code: Currency for new account (if created)

        Returns:
            Account ID or None if not found and create_if_missing is False
        """
        self._load_accounts()

        key = f"account:{account_type}:{name.lower().strip()}"

        if key in self._cache:
            return self._cache[key]

        if not create_if_missing:
            return None

        # Create the account
        try:
            account_id = self.client.find_or_create_account(
                name=name,
                account_type=account_type,
                currency_code=currency_code,
            )
            self._cache[key] = account_id
            logger.info(f"Created account '{name}' ({account_type}) with ID {account_id}")
            return account_id
        except Exception as e:
            logger.error(f"Failed to create account '{name}': {e}")
            return None

    def resolve_category(
        self,
        name: str,
        create_if_missing: bool = True,
    ) -> int | None:
        """
        Resolve category name to Firefly ID.

        Args:
            name: Category name to resolve
            create_if_missing: If True, create the category if not found

        Returns:
            Category ID or None if not found and create_if_missing is False
        """
        self._load_categories()

        key = f"category:{name.lower().strip()}"

        if key in self._cache:
            return self._cache[key]

        if not create_if_missing:
            return None

        # Create the category
        try:
            category_id = self.client.create_category(name=name)
            self._cache[key] = category_id
            logger.info(f"Created category '{name}' with ID {category_id}")
            return category_id
        except Exception as e:
            logger.error(f"Failed to create category '{name}': {e}")
            return None

    def resolve_tag(
        self,
        name: str,
        create_if_missing: bool = True,
    ) -> int | None:
        """
        Resolve tag name to Firefly ID.

        Args:
            name: Tag name to resolve
            create_if_missing: If True, create the tag if not found

        Returns:
            Tag ID or None if not found and create_if_missing is False
        """
        self._load_tags()

        key = f"tag:{name.lower().strip()}"

        if key in self._cache:
            return self._cache[key]

        if not create_if_missing:
            return None

        # Create the tag
        try:
            tag_id = self.client.create_tag(tag=name)
            self._cache[key] = tag_id
            logger.info(f"Created tag '{name}' with ID {tag_id}")
            return tag_id
        except Exception as e:
            logger.error(f"Failed to create tag '{name}': {e}")
            return None

    def resolve_budget(
        self,
        name: str,
        create_if_missing: bool = True,
    ) -> int | None:
        """
        Resolve budget name to Firefly ID.

        Args:
            name: Budget name to resolve
            create_if_missing: If True, create the budget if not found

        Returns:
            Budget ID or None if not found and create_if_missing is False
        """
        self._load_budgets()

        key = f"budget:{name.lower().strip()}"

        if key in self._cache:
            return self._cache[key]

        if not create_if_missing:
            return None

        # Create the budget
        try:
            budget_id = self.client.create_budget(name=name)
            self._cache[key] = budget_id
            logger.info(f"Created budget '{name}' with ID {budget_id}")
            return budget_id
        except Exception as e:
            logger.error(f"Failed to create budget '{name}': {e}")
            return None

    def resolve_rule_group(
        self,
        title: str,
        create_if_missing: bool = True,
    ) -> int | None:
        """
        Resolve rule group title to Firefly ID.

        Args:
            title: Rule group title to resolve
            create_if_missing: If True, create the rule group if not found

        Returns:
            Rule group ID or None if not found and create_if_missing is False
        """
        self._load_rule_groups()

        key = f"rule_group:{title.lower().strip()}"

        if key in self._cache:
            return self._cache[key]

        if not create_if_missing:
            return None

        # Create the rule group
        try:
            rule_group_id = self.client.create_rule_group(title=title)
            self._cache[key] = rule_group_id
            logger.info(f"Created rule group '{title}' with ID {rule_group_id}")
            return rule_group_id
        except Exception as e:
            logger.error(f"Failed to create rule group '{title}': {e}")
            return None

    def clear_cache(self) -> None:
        """Clear all cached references."""
        self._cache.clear()
        self._account_cache_loaded = False
        self._category_cache_loaded = False
        self._tag_cache_loaded = False
        self._budget_cache_loaded = False
        self._rule_group_cache_loaded = False
