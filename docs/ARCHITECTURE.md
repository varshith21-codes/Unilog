# AXIOM / Unilog Architecture

This diagram describes the architecture implemented in this repository. Solid arrows are live runtime paths. Dashed arrows are optional tooling, fallback behavior, or infrastructure that is provisioned but not connected to the current application.

```mermaid
flowchart LR
    user["Operator<br/>Web browser"]

    subgraph host["Single-host Docker Compose — current runtime"]
        direction LR
        caddy["Caddy 2<br/>TLS · Basic Auth · compression<br/>ports 80 / 443"]
        next["Next.js 16 console<br/>React UI + Server Components<br/>Server Actions + file proxies<br/>internal port 3000"]
        api["FastAPI / Uvicorn<br/>single process · synchronous work<br/>internal port 8000"]
        tmp["Ephemeral /tmp<br/>512 MiB upload and parsing space"]

        caddy -->|"all application routes"| next
        caddy -->|"/api/artifact/:sha256 only"| api
        next -->|"private BFF calls over AXIOM_API_URL"| api
        api --> tmp
    end

    user -->|"HTTPS"| caddy

    fixture["Console fixture JSON<br/>dataset fallback only"]
    next -.->|"API unavailable"| fixture

    subgraph core["Shared Python application and domain packages"]
        direction TB
        enrich["Single-SKU orchestration<br/>source resolution + enrich_one"]
        retrieve["Retrieval and ingest<br/>library · manufacturer site · search<br/>policy / robots / SSRF controls"]
        stages["Pipeline stage orchestrator<br/>classify → extract → normalize → record<br/>validate → confidence → syndicate<br/>optional copy / reasoning → certificate"]
        delivery["Governed delivery pipeline<br/>offline batch + 252-column export"]
        review["Review and console projections<br/>decisions · priors · dashboard overlays"]
        schema["Governed YAML schemas<br/>product classes + delivery format"]

        enrich --> retrieve
        enrich --> stages
        stages --> schema
        stages --> review
        delivery --> schema
    end

    api -->|"POST /api/enrich<br/>single-flight; 429 when busy"| enrich
    api -->|"POST /api/delivery/export<br/>offline, no model calls"| delivery
    api -->|"dataset, policy, sessions,<br/>decisions, downloads"| review

    subgraph external["External runtime dependencies"]
        direction TB
        bedrock["AWS Bedrock Runtime<br/>classification · extraction<br/>optional copy / reasoning"]
        web["Manufacturer websites<br/>Brave / DuckDuckGo / Bedrock search<br/>optional Playwright rendering"]
    end

    stages -->|"model calls"| bedrock
    retrieve -->|"policy-gated HTTPS"| web

    data[("Host bind mount: data/<br/>current system of record<br/><br/>sessions · console bundles<br/>content-addressed source artifacts<br/>document library index<br/>calibration / priors<br/>cross-source / equivalence overlays<br/>CSV / XLSX / provenance exports")]
    api <-->|"atomic local-file reads and writes"| data
    retrieve <-->|"artifact and library reuse"| data
    review <-->|"persisted projections"| data
    delivery -->|"persist enrichment outputs"| data

    subgraph tools["Optional and operational entry points"]
        direction TB
        cli["Pipeline / delivery / backtest CLIs"]
        mcp["Optional MCP catalogue server<br/>stdio or HTTP · not in Compose"]
    end

    cli -.->|"same shared stages"| stages
    cli -.-> data
    mcp -.->|"reads catalogue bundles"| data

    subgraph future["AWS CDK storage foundation — provisioned, not wired to runtime"]
        direction TB
        landing[("Versioned S3<br/>landing bucket")]
        artifacts[("S3<br/>derived artifacts")]
        jobs[("DynamoDB<br/>jobs + idempotency")]
        queue["SQS ingest queue"]
        dlq["SQS dead-letter queue"]
        absent["No Lambda or worker consumer<br/>implemented in this repository"]

        queue -->|"failed messages"| dlq
        queue -.-> absent
    end

    classDef runtime fill:#e8f1ff,stroke:#2563eb,color:#172554,stroke-width:1.5px;
    classDef store fill:#ecfdf5,stroke:#059669,color:#064e3b,stroke-width:1.5px;
    classDef externalSvc fill:#fff7ed,stroke:#ea580c,color:#7c2d12,stroke-width:1.5px;
    classDef futureSvc fill:#f5f3ff,stroke:#7c3aed,color:#4c1d95,stroke-width:1.5px,stroke-dasharray:5 5;
    classDef note fill:#f8fafc,stroke:#64748b,color:#334155,stroke-dasharray:4 4;

    class caddy,next,api,enrich,retrieve,stages,delivery,review,schema runtime;
    class data,landing,artifacts,jobs store;
    class bedrock,web externalSvc;
    class queue,dlq futureSvc;
    class fixture,tmp,cli,mcp,absent note;
```

## Key architectural constraints

- **Next.js is the browser-facing BFF.** Server Components, Server Actions, and route handlers call FastAPI on the private Compose network. The only browser-to-FastAPI exception is the hash-addressed artifact route proxied by Caddy.
- **The API is deliberately single-process.** Enrichment runs synchronously under a process mutex; there is no live queue, background worker, Redis, or SQL database.
- **The bind-mounted `data/` directory is the current system of record.** It holds mutable review sessions, immutable/content-addressed sources, dashboard projections, calibration state, and delivery outputs.
- **Online and offline paths share domain code.** HTTP enrichment and operational CLIs converge on the same staged Python pipeline; upload-based delivery uses the deterministic governed delivery package and does not call Bedrock.
- **The CDK resources are future foundation.** S3, DynamoDB, SQS, and the DLQ are defined, but the current API and pipeline have no adapters or consumer connecting them.

## Primary implementation references

- Runtime topology: [`compose.yaml`](../compose.yaml) and [`deploy/Caddyfile`](../deploy/Caddyfile)
- API and persistence boundaries: [`apps/api/main.py`](../apps/api/main.py)
- Console BFF actions and loaders: [`apps/console/src/lib/actions.ts`](../apps/console/src/lib/actions.ts) and [`apps/console/src/lib/data.ts`](../apps/console/src/lib/data.ts)
- Enrichment and pipeline orchestration: [`packages/axiom/pipeline/single.py`](../packages/axiom/pipeline/single.py) and [`packages/axiom/pipeline/stages.py`](../packages/axiom/pipeline/stages.py)
- Future AWS storage foundation: [`infra/lib/storage-stack.ts`](../infra/lib/storage-stack.ts)
