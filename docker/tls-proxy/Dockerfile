FROM nginx:1.29-alpine

RUN apk add --no-cache openssl

COPY nginx.conf /etc/nginx/nginx.conf
COPY docker-entrypoint.sh /usr/local/bin/tjarepo-tls-entrypoint
RUN chmod +x /usr/local/bin/tjarepo-tls-entrypoint

ENTRYPOINT ["/usr/local/bin/tjarepo-tls-entrypoint"]
CMD ["nginx", "-g", "daemon off;"]
