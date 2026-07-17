# Zucchini Connector UI

Svelte 5/Vite operator dashboard using source-owned shadcn-svelte components.

```sh
npm install
npm run dev
```

The development server proxies `/api` to the connector at
`https://localhost:8443`. `npm run build` emits the production application to
`../app/static`, where FastAPI serves it under `/ui/`.
