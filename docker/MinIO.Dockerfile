FROM golang:1.24.9-bookworm AS build
ENV CGO_ENABLED=0
RUN go install github.com/minio/minio@9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a
RUN cp /go/pkg/mod/github.com/minio/minio@*/LICENSE /minio-LICENSE
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/*
COPY --from=build /go/bin/minio /usr/local/bin/minio
COPY --from=build /minio-LICENSE /usr/share/doc/minio/LICENSE
RUN install -d -o 10001 -g 10001 /data
USER 10001:10001
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
