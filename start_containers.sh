echo "Starting PostgreSQL…"
sudo systemctl start postgresql@18-main

echo "Starting Docker services (Redis, OpenSearch, Addressr)…"
sudo systemctl start docker
docker compose --file ../underground-crm/docker-compose.yml up