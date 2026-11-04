from django.conf import settings
from django.contrib.sites.models import Site as DjangoSite
from django.test import TestCase
from django.urls import reverse
from robots.models import Rule, Url
from wagtail.models import Site as WagtailSite

from underground_crm.models import BasicPage

# The Host header of the simulated request. Django's test client calls the view in this
# process, so nothing is fetched over the network; the header only stands in for the domain
# a deployment would be reached on. It is under the reserved .example TLD (RFC 2606) so
# that it cannot be mistaken for, or ever resolve to, a real website. ALLOWED_HOSTS has to
# accept it, as it would a real deployment's domain, for the request to be served at all.
PUBLIC_HOST = "www.peoples-party.example"
# A fresh django.contrib.sites Site is named after example.com until somebody renames it
# by hand, so robots.txt must not take the sitemap's host from it.
UNCONFIGURED_DJANGO_SITE_DOMAIN = "example.com"
PRIVATE_PATH = "/django-admin/"
CRAWLER = "*"
VOLUNTEER_PAGE_TITLE = "Volunteer with us"
VOLUNTEER_PAGE_SLUG = "volunteer"


class RobotsTxtTest(TestCase):
    def setUp(self) -> None:
        DjangoSite.objects.filter(pk=settings.SITE_ID).update(
            domain=UNCONFIGURED_DJANGO_SITE_DOMAIN
        )

    def request_robots_txt(self) -> str:
        """robots.txt as this codebase renders it for a request to PUBLIC_HOST."""
        with self.settings(ALLOWED_HOSTS=[PUBLIC_HOST]):
            response = self.client.get("/robots.txt", HTTP_HOST=PUBLIC_HOST)
        self.assertEqual(response.status_code, 200, "robots.txt is public.")
        self.assertEqual(
            response["Content-Type"].split(";")[0],
            "text/plain",
            "Crawlers expect robots.txt to be plain text.",
        )
        return response.content.decode()

    def test_without_rules_every_crawler_may_crawl_everything(self) -> None:
        lines = self.request_robots_txt().splitlines()
        self.assertIn("User-agent: *", lines)
        self.assertIn(
            "Disallow:",
            lines,
            "An empty Disallow line is how robots.txt says that nothing is off limits.",
        )

    def test_advertises_the_sitemap_on_the_host_that_was_asked(self) -> None:
        sitemap_line = f"Sitemap: http://{PUBLIC_HOST}{reverse('sitemap')}"
        body = self.request_robots_txt()
        self.assertIn(sitemap_line, body.splitlines())
        self.assertNotIn(
            UNCONFIGURED_DJANGO_SITE_DOMAIN,
            body,
            "The django.contrib.sites domain is a placeholder, so naming it would send "
            "crawlers to somebody else's website.",
        )

    def test_omits_the_obsolete_host_directive(self) -> None:
        self.assertFalse(
            any(line.startswith("Host:") for line in self.request_robots_txt().splitlines()),
            "ROBOTS_USE_HOST is off, so no Host line should be written.",
        )

    def test_lists_a_disallowed_path_from_the_admin(self) -> None:
        rule = Rule.objects.create(robot=CRAWLER)
        rule.sites.add(DjangoSite.objects.get(pk=settings.SITE_ID))
        rule.disallowed.add(Url.objects.create(pattern=PRIVATE_PATH))

        self.assertIn(f"Disallow: {PRIVATE_PATH}", self.request_robots_txt().splitlines())


class SitemapTest(TestCase):
    def test_lists_live_pages(self) -> None:
        wagtail_site = WagtailSite.objects.get(is_default_site=True)
        page = BasicPage(title=VOLUNTEER_PAGE_TITLE, slug=VOLUNTEER_PAGE_SLUG)
        wagtail_site.root_page.add_child(instance=page)

        response = self.client.get(reverse("sitemap"))

        self.assertEqual(response.status_code, 200, "The sitemap is public.")
        self.assertContains(
            response,
            f"<loc>{page.get_full_url()}</loc>",
            msg_prefix="A published page should be offered to crawlers",
        )
