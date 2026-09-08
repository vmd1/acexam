import os
from fastapi import APIRouter, Header, HTTPException, Depends
from database import get_db
from spec_code_utils import spec_code_slug

router = APIRouter()

# Shared secret between this endpoint and the Traefik container's
# --providers.http.headers config (docker-compose.yml) - not a user/admin
# auth boundary, just enough to keep this off-menu for anything that isn't
# Traefik itself, since the endpoint has to be reachable without a login
# cookie (Traefik can't authenticate as a user).
TRAEFIK_INTERNAL_TOKEN = os.getenv("TRAEFIK_INTERNAL_TOKEN", "dev_traefik_token")


@router.get("/traefik-config")
async def traefik_config(x_internal_token: str | None = Header(None), db=Depends(get_db)):
    """
    §6.5 multi-adapter serving: Traefik's HTTP provider polls this on an
    interval (docker-compose.yml, --providers.http.pollInterval) and
    reconfigures its own routing live from whatever it gets back - no file
    to regenerate, no restart, no script to remember to rerun. Flipping a
    spec code's status to 'live' (or changing its serving_port) in
    spec_code_marking_models takes effect within one poll interval.

    Every live spec code with a serving_port gets a route from
    /ai/mark/<slug>/... (its own mlx_lm.server process, reached via
    host.docker.internal:<port> since MLX can't run in this container -
    see ai_pipeline.py) with that prefix stripped before forwarding.
    """
    if x_internal_token != TRAEFIK_INTERNAL_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not db:
        # Empty config, not an error - Traefik just serves no routes until
        # the DB is reachable again, rather than the poll itself failing.
        return {"http": {"routers": {}, "middlewares": {}, "services": {}}}

    async with db.acquire() as conn:
        rows = await conn.fetch('''
            SELECT exam_board, level, subject, tier, serving_port
            FROM spec_code_marking_models
            WHERE status = 'live' AND serving_port IS NOT NULL
        ''')

    routers, middlewares, services = {}, {}, {}
    for row in rows:
        slug = spec_code_slug(row["exam_board"], row["level"], row["subject"], row["tier"])
        path_prefix = f"/ai/mark/{slug}"
        routers[f"ai-mark-{slug}"] = {
            "rule": f"PathPrefix(`{path_prefix}`)",
            "service": slug,
            "middlewares": [f"strip-{slug}"],
        }
        middlewares[f"strip-{slug}"] = {"stripPrefix": {"prefixes": [path_prefix]}}
        services[slug] = {
            "loadBalancer": {
                "servers": [{"url": f"http://host.docker.internal:{row['serving_port']}"}]
            }
        }

    return {"http": {"routers": routers, "middlewares": middlewares, "services": services}}
