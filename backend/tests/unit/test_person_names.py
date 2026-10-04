from __future__ import annotations

import pytest

from core.application.person_names import UNKNOWN_PERSON, PersonNames, person_name
from core.domain.directory import DirectoryUser
from core.domain.graph import Developer
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore

TENANT = "demo"
# Made-up chat ids.
CHAT_ID = "U0123ABCD"
OTHER_CHAT_ID = "U0456EFGH"


def _names(store: InMemoryGraphStore) -> PersonNames:
    return PersonNames(
        graph_repository=store, directory_repository=InMemoryDirectoryUserRepository(store)
    )


async def test_a_member_made_by_hand_is_found_by_their_chat_id() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(
        Developer(
            tenant_id=TENANT, id="dev-1", name="Rosa Lind", metadata={"chat_external_id": CHAT_ID}
        )
    )

    assert await _names(store).resolve(TENANT, [CHAT_ID, "dev-1"]) == {
        CHAT_ID: "Rosa Lind",
        "dev-1": "Rosa Lind",
    }


async def test_a_members_own_id_wins_over_another_members_chat_id() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(Developer(tenant_id=TENANT, id=CHAT_ID, name="Rosa Lind"))
    await store.upsert_node(
        Developer(
            tenant_id=TENANT,
            id="dev-2",
            name="Kai Thompson",
            metadata={"chat_external_id": CHAT_ID},
        )
    )

    assert await _names(store).resolve(TENANT, [CHAT_ID]) == {CHAT_ID: "Rosa Lind"}


async def test_a_name_that_is_only_an_id_is_no_name() -> None:
    store = InMemoryGraphStore()
    # The member record holds only the id, so the directory's name is used.
    await store.upsert_node(Developer(tenant_id=TENANT, id=CHAT_ID, name=CHAT_ID))
    await store.upsert_users(
        [
            DirectoryUser(tenant_id=TENANT, external_id=CHAT_ID, display_name="Rosa L."),
            # The directory holds only the id: nothing names this person.
            DirectoryUser(tenant_id=TENANT, external_id=OTHER_CHAT_ID, display_name=OTHER_CHAT_ID),
        ]
    )

    names = await _names(store).resolve(TENANT, [CHAT_ID, OTHER_CHAT_ID, "  "])

    assert names == {CHAT_ID: "Rosa L."}
    assert person_name(names, OTHER_CHAT_ID, None) == UNKNOWN_PERSON


@pytest.mark.parametrize(
    ("resolved", "recorded", "expected"),
    [
        # The member or directory name beats a name recorded with the data.
        ({CHAT_ID: "Rosa Lind"}, "Rosa L.", "Rosa Lind"),
        # A recorded name is used when nothing else names the person...
        ({}, "Rosa L.", "Rosa L."),
        # ...unless it is only the id.
        ({}, CHAT_ID, UNKNOWN_PERSON),
        ({}, None, UNKNOWN_PERSON),
    ],
)
def test_person_name_never_falls_back_to_the_id(
    resolved: dict[str, str], recorded: str | None, expected: str
) -> None:
    assert person_name(resolved, CHAT_ID, recorded) == expected
