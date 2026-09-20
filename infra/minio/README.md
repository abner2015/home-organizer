# MinIO bucket setup

Buckets are created automatically by the `minio-init` service in
`docker-compose.yml` (uses the `minio/mc` image):

- `home-organizer-items` — long-lived item assets
- `home-organizer-uploads` — transient pre-item uploads

Both buckets are private (`mc anonymous set none`). All access from the API
goes through presigned URLs with bounded TTL — there are no public bucket
policies and no long-lived download URLs.

## Manual setup (if you skip the init container)

```sh
mc alias set local http://localhost:9000 homeorg homeorg-minio
mc mb -p local/home-organizer-items
mc mb -p local/home-organizer-uploads
mc anonymous set none local/home-organizer-items
mc anonymous set none local/home-organizer-uploads
```
