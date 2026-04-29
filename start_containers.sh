echo "Starting PostgreSQL…"
sudo systemctl start postgresql@18-main

echo "Starting Redis…"
sudo systemctl start docker
docker compose --file ../underground-crm/docker-compose.yml up