# PARAMA CRM frontend

The frontend is a Vue 3 application served by the CRM backend in production.

## Development

From this directory:

```bash
yarn install --frozen-lockfile
yarn dev --host 127.0.0.1
```

The development server is available at `http://crm.localhost:8080/crm` and
proxies backend requests to the local Bench site on port 8000. Keep CSRF
protection enabled; the development proxy and boot data provide the token.

## Verification

```bash
yarn test:run
yarn eslint src tests/unit --max-warnings=0
yarn build
```

The production build is written to `crm/public/frontend`, and its HTML entry is
copied to `crm/www/crm.html`.
