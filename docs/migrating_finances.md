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

## Importing donation history
`python manage.py import_donations --csv-file <financial transactions export>` records each successful transaction in
the export as a `Donation` (and a "donated" `Engagement`), dated when it succeeded, so that each person's donation
history survives the migration. Run it after `import_people_csv`. You can try it first with `--dry-run`.

Failed, pending and refunded transactions are counted but not imported. Re-running the command never duplicates a
donation, since each is keyed by its legacy transaction ID (`Donation.legacy_id`). The legacy payment processor's ID
for each transaction is kept in the donation's metadata rather than as its `stripe_payment_id`, since any Stripe charge
it names belongs to NationBuilder's Stripe account. People's donation totals are left as `import_people_csv` set them.

## Recreating recurring donations
Once Stripe has copied the legacy Customers (with their cards) into your site's Stripe account, there are management
commands to recreate the recurring donations.

NationBuilder can provide you with a CSV of the recurring donations, but it lacks people's names or email addresses,
so matching a Stripe Customer to an Underground-CRM Person and creating a new subscription did not end up being
straightforward.



## If you do not have an export of the recurring donors
1. `python manage.py fetch_recurring_donations --output-dir <dir>` saves every page of the legacy CRM's listing of
   recurring plans, starting from `LEGACY_RECURRING_DONATIONS_URL` and using the admin cookie file.
2. `python manage.py migrate_recurring_donations --input-dir <dir> --csv-file <path> --payment-page-id <id>` pairs each
   active plan with a row of NationBuilder's CSV of recurring donations (see above), labels the row's Customer with the
   donor's legacy person ID, name and email address, and creates a subscription first billing at the row's next payment.
   The Customers that Stripe copied have no name or email address, so the listing and the CSV are the only way to
   learn who each one is. Fetch the listing and the CSV again shortly before running this, since plans whose next
   payment has passed are skipped.

   A plan's row is the one whose `nationbuilder_recurring_donation_id` is the plan's ID. A plan with no row, or whose
   Customer is already labeled as another person, is skipped with a warning, and the rows that matched no active plan
   are listed at the end. Run with `--dry-run` first, and check the warnings.

Cancel each plan in the legacy CRM as soon as its subscription exists, and before its next bill date, or the donor will
be charged twice.