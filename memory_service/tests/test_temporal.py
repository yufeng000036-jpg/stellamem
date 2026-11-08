import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from store import Principal, Denied
import test_service


class TemporalTests(test_service.MemoryTests):
    def test_fact_time_travel(self):
        old = self.active(
            title="Address",
            content="Address was HarborCity",
            scope="public",
            valid_from="2024-01-01T00:00:00Z",
        )
        new = self.s.supersede(
            self.p,
            old["id"],
            {"content": "Address is MountainCity", "source": {"reference": "user:correction"}},
            old["version"],
            "2025-01-01T00:00:00Z",
        )
        public = Principal(role="public")
        self.assertEqual(self.s.context(public, "Address")["memories"][0]["id"], new["id"])
        past = self.s.context(public, "Address", as_of="2024-06-01T00:00:00Z")["memories"]
        self.assertEqual([x["id"] for x in past], [old["id"]])

    def test_owner_reader_cannot_write_or_read_pending(self):
        r = self.active()
        self.add(title="Pending private")
        token = self.s.issue_client(self.p, "owner-qq", "owner_reader", "owner", "qq:owner", "qq")[
            "token"
        ]
        p = self.s.authenticate(token)
        self.assertEqual(len(self.s.context(p, "ocean")["memories"]), 1)
        self.assertEqual(self.s.stats(p)["total"], 1)
        with self.assertRaises(Denied):
            self.s.create(p, {"title": "a", "content": "b"})
        with self.assertRaises(Denied):
            self.s.history(p, r["id"])

    def test_same_nickname_is_not_identity(self):
        for identity in ["qq:123", "bili:123"]:
            p = Principal(actor=identity, role="participant", conversation=identity)
            r = self.s.create(
                p,
                {
                    "title": "nickname same",
                    "content": "account " + identity,
                    "source": {"reference": "platform:" + identity},
                },
            )
            self.s.review(self.p, r["id"], "active", 1)
        p = Principal(actor="qq:123", role="participant", conversation="qq:123")
        self.assertTrue(
            all("bili" not in x["content"] for x in self.s.context(p, "nickname")["memories"])
        )

    def test_cross_channel_public_private_matrix(self):
        for i in range(20):
            a = Principal(
                actor="person" + str(i),
                role="participant",
                conversation="qq:" + str(i),
                channel="qq",
            )
            r = self.s.create(
                a,
                {
                    "title": "topic" + str(i),
                    "content": "private topic" + str(i),
                    "source": {"reference": "qq:message" + str(i)},
                },
            )
            self.s.review(self.p, r["id"], "active", 1)
            pub = self.active(
                title="topic" + str(i), content="public topic" + str(i), scope="public"
            )
            for channel in ["bili", "douyin", "live"]:
                consumer = Principal(actor="viewer" + str(i), role="public", channel=channel)
                result = self.s.context(consumer, "topic" + str(i))["memories"]
                self.assertTrue(result)
                self.assertTrue(all(x["id"] == pub["id"] for x in result))

    def test_historical_private_not_public(self):
        r = self.active(
            title="address", content="private history", valid_from="2024-01-01T00:00:00Z"
        )
        self.s.supersede(
            self.p,
            r["id"],
            {"scope": "public", "content": "public current", "source": {"reference": "owner"}},
            r["version"],
            "2025-01-01T00:00:00Z",
        )
        self.assertEqual(
            self.s.context(Principal(role="public"), "address", as_of="2024-06-01T00:00:00Z")[
                "memories"
            ],
            [],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
