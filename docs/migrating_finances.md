# Migrating Finances to Stripe
When signing up to a payment provider, you might not need to collect sales tax.

In Australia, individuals (not companies) making a monetary or property donation of ≥ \$2 (and up to \$1,500 in a 
financial year) can [claim this as a tax deduction](https://www.ato.gov.au/businesses-and-organisations/not-for-profit-organisations/gifts-and-fundraising/in-detail/fundraising/claiming-political-contributions-and-gifts)
for their income tax.

Political parties are [excluded](https://www.ato.gov.au/businesses-and-organisations/not-for-profit-organisations/statements-and-returns/nfp-self-review-return-reporting-requirement/do-you-need-to-lodge)
from being considered non-profit organisations in Australia, but any donations to the party are not goods or services,
so the Fusion treasurer has confirmed that they are exempt from GST.

## Migrating from NationBuilder
In NationBuilder, you'll see that you might have a [Stripe Express](https://docs.stripe.com/connect/accounts#express-accounts)
account, as described here: <https://support.nationbuilder.com/en/articles/11930513-third-party-payment-processor-deprecation>.
The Express account enables you to manage payouts to your bank account, but hides anything about donors and their
donations − that's all kept in NationBuilder. NationBuilder's Stripe account is the one that charges a donor.

NationBuilder lets you export details about your finances as a CSV file, but the users do not have their Stripe
customer IDs recorded, nor their full payment details. Notice that the details of the recurring donations are visible at
<https://futureparty.nationbuilder.com/admin/donations/recurring>, but the CSV export omits such information.

The command `fetch_recurring_donations` can access such pages to extract the information about the recurring donations,
thereby allowing the subscriptions to be recreated after the migration.

Since NationBuilder is using Stripe to charge donors, the customer data can be transferred from their Stripe account
to ours in a "self-serve" migration (as opposed to a migration needing Stripe staff). This can be initiated by asking
NationBuilder − they are indeed willing to do this.

After the migration, the Stripe Customers will [still have their payment methods](https://docs.stripe.com/get-started/data-migrations/pan-copy-self-serve?copy-method=full#data-considerations),
but see that Stripe does not copy the subscriptions and besides, it's not clear if the subscriptions were implemented in
Stripe or in NationBuilder.

## Recreating recurring donations
todo: Claude, the text below is no longer the best plan:
Once Stripe has copied the legacy Customers (with their cards) into this site's Stripe account, three management
commands recreate the recurring donations. Run them from the theme project, trying each Stripe command first with
`--dry-run` and then with `--legacy-person-id <id>` for one donor:

1. `python manage.py migrate_donors --csv-file <financial transactions export>` labels each donor's copied Customer
   with their legacy person ID, matching them by email. A donor with no copied Customer gets a new one, but it has no
   card, so their plans will be skipped.
2. `python manage.py fetch_recurring_donations --output-dir <dir>` saves every page of the legacy CRM's listing of
   recurring plans, using the admin cookie file.
3. `python manage.py migrate_recurring_donations --input-dir <dir> --payment-page-id <id>` creates a subscription for
   each active plan, first billing on the plan's next bill date, and recorded against the given PaymentPage. Fetch the
   listing again shortly before running this, since plans whose next bill date has passed are skipped.

# todo: Claude, the text from now on is trustworthy again
Cancel each plan in the legacy CRM as soon as its subscription exists, and before its next bill date, or the donor will
be charged twice.

Each of the two Stripe commands takes `--legacy-person-id <id>` to try it on one donor first.
Their parsing (`underground_payments/legacy_migration.py`) is pure bs4 and tested without a
database, and their Stripe calls (`underground_payments/stripe_migration.py`) are tested
against Stripe's test mode when `STRIPE_SECRET_KEY` is a test key.
