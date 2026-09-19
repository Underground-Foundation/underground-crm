from underground_crm.test.test_import_blog_post_stubs import *


class Probe(LegacyBlogDirectoryTestCase):
    def test_probe(self):
        self.run_import()
        p = FeedPage.objects.get(slug=BLOG_SLUG)
        print(
            "PROBE feed",
            p.live,
            p.has_unpublished_changes,
            p.live_revision_id,
            p.latest_revision_id,
            p.go_live_at,
            p.first_published_at,
        )
        for b in BlogPost.objects.all():
            print(
                "PROBE post",
                b.slug,
                b.live,
                b.has_unpublished_changes,
                b.live_revision_id,
                b.latest_revision_id,
            )
