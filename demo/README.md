# MM-Agent Demo

## Requirements

- Python 3.10+
- Node.js 20+
- npm
- LLM API key
- E2B API key for sandbox execution. Sign in or register at https://e2b.dev/ to create one.

## Quick Start

From the repository root:

```bash
cp demo/.env.example demo/.env
cp demo/frontend/.env.local.example demo/frontend/.env.local
```

Edit `demo/.env`:

```env
OPENAI_API_KEY=your_openai_key
DEEPSEEK_API_KEY=your_deepseek_key
BASE_URL=https://api.openai.com/v1
DEEPSEEK_BASE_URL=https://api.deepseek.com
MODEL_NAME=gpt-5.6-sol
AGENT_MODEL_NAME=gpt-5.6-sol
E2B_API_KEY=your_e2b_key
```

To use DeepSeek V4 Pro, set `MODEL_NAME` and `AGENT_MODEL_NAME` to
`deepseek-v4-pro`; its provider-specific key/base are selected automatically.
The backend requires LiteLLM 1.93.0 or newer for GPT-5.6 model metadata.
The routing rules have deterministic tests, but live OpenAI/DeepSeek calls and
the E2B end-to-end path still require your own credentials and were not run in
the repository's credential-free validation environment.

Remote custom LLM hosts are denied unless an administrator adds their exact
hostname to `MMAGENT_ALLOWED_LLM_HOSTS`. DNS is checked for non-global targets;
production deployments that enable custom hosts should also enforce an egress
proxy/firewall allowlist to close DNS-rebinding time-of-check/time-of-use gaps.

Start the demo:

```bash
bash demo/scripts/run.sh
```

Open:

- Frontend: http://localhost:3000
- Backend: http://localhost:8000

There is no default account or password. Keep registration disabled for a
shared deployment; for local bootstrap, explicitly enable `SEED_LOCAL_ADMIN`
and set a strong `LOCAL_ADMIN_PASSWORD`, then disable seeding again.

## Commands

```bash
bash demo/scripts/run.sh       # install dependencies, build, and start
bash demo/scripts/status.sh    # show backend/frontend status
bash demo/scripts/stop.sh      # stop backend/frontend
bash demo/scripts/clean.sh     # remove generated runtime/build files
bash demo/scripts/clean.sh --all  # also remove backend/.venv and frontend/node_modules
```

Development mode:

```bash
FRONTEND_MODE=dev bash demo/scripts/run.sh
```

## Dependency Files

- Backend Python dependencies: `demo/requirements.txt`
- Frontend dependencies: `demo/frontend/package.json`
- Frontend lockfile: `demo/frontend/package-lock.json`

## Notes

- SQLite data and uploaded files are stored under `demo/runtime/` and `demo/backend/runtime/`.
- Browser settings can override LLM/E2B keys through `X-LLM-*` and `X-E2B-API-Key` headers.
- Redis is optional. Leave `REDIS_URL` empty for local single-process mode.
- `SANDBOX_TIMEOUT` controls the E2B sandbox lease in seconds. Reused sandboxes
  renew this lease automatically. E2B currently allows up to 3600 seconds on
  Hobby plans and 86400 seconds on Pro plans.

## Non-Commercial Use

This demo is provided for research, evaluation, education, and other non-commercial use only.
Commercial use, resale, hosted service operation, or integration into paid products requires prior written permission from the project owner.
