#!/bin/bash

savedCountryPopulation=$(grep --only-matching --perl-regexp '"[A-Z]+",?' app_settings.py | wc --lines)
stripePopulation=$(curl "https://stripe.com/au/global" 2>/dev/null | grep --only-matching --perl-regexp "country=[a-zA-Z]+" | grep --only-matching --perl-regexp "=[a-zA-Z]+" | grep --only-matching --perl-regexp "[a-zA-Z]+" | wc --lines)

if [ "$savedCountryPopulation" != "$stripePopulation" ]; then
  echo "There are ${stripePopulation} countries at https://stripe.com/au/global, but ${savedCountryPopulation} countries saved here"
  exit 1
else
  echo "✅ Country population matches at stripe.com: ${savedCountryPopulation} supported countries"
fi
