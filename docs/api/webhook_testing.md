# Testing the approval webhook flow

The **approval inbox** (`scripts/dev/approval_inbox.py`) plays an external approval system, such as ServiceNow, a Slack bot or an e-mail gateway. It exercises the full loop using real HTTP and real signatures.

```
finagent ──approval.requested (HMAC-signed)──▶ approval inbox  (shows explanation + suggested approvers)
finagent ◀──POST /webhooks/inbound/approval (HMAC-signed)── approval inbox  (your decision)
finagent ──approval.resolved, decision.submitted, run.completed──▶ approval inbox
```

## One command: watch the whole loop

The service must be running first (`make start` or `make up`).

| OS | Command |
|---|---|
| macOS / Linux | `make webhook-demo` or `make webhook-demo CASES="FIN-012 FIN-018"` |
| Windows | `.\dev.ps1 webhook-demo` or `.\dev.ps1 webhook-demo FIN-012 FIN-018` |
| Any | `python scripts/dev/devctl.py webhook-demo` |

It runs these steps:

1. Subscribes to the webhooks.
2. Starts the cases (default: FIN-001, FIN-012, FIN-002, FIN-003).
3. Receives each `approval.requested` and **verifies its signature**.
4. Picks approvers that satisfy every required slot, for example a Cost Centre Manager plus Financial Control for a new vendor.
5. Sends each decision **twice**, to show the second is replayed with no effect.
6. Prints `decision.submitted` / `run.completed` and a final table.
7. Unsubscribes.

Sample output:

```
<- approval.requested  run run-751431584d73  (signature ok)
   APPROVAL REQUEST apr-0559d119a890  case FIN-012  vendor V-1008  invoice HDS-0001  AUD 8800.00
   needs: [primary] Approver with authority >= AUD 8800.00 (minimum role: Cost Centre Manager)
   needs: [financial_control] Financial Control co-approval (higher-risk: new vendor (13 days old))
   suggested approvers: financial_control=u-fc-02, primary=u-ccm-01
-> APPROVE as u-fc-02 [financial_control] (sent)  HTTP 200  status=PENDING effect=recorded replayed=False
-> APPROVE as u-fc-02 [financial_control] (duplicate re-send)  HTTP 200  status=PENDING replayed=True
-> APPROVE as u-ccm-01 [primary] (sent)  HTTP 200  status=APPROVED effect=recorded
<- decision.submitted  posted (simulated): SIM-POST-F3A7F979CD [POST, ACCEPTED]
<- run.completed  case FIN-012 -> APPROVE_FOR_POSTING
```

## Interactive: you are the approver

```bash
make inbox                    # or: .\dev.ps1 inbox   |   python scripts/dev/devctl.py inbox
make load CASES=FIN-001       # in a second terminal (or POST /runs in Swagger)
```

Each request is shown with its explanation. You then choose:

- `a`: approve with the suggested approvers;
- `r`: reject;
- `s`: skip, leaving it pending;
- or type any approver id, such as `u-ccm-01`, to try a refusal.

Other modes:

| Option | Effect |
|---|---|
| `--auto approve` / `--auto reject` | decide every request automatically |
| `--duplicate` | send each callback twice |
| `--port 9099` | listening port (default 9099; if busy, a free port is chosen automatically) |
| `--public-url http://host.docker.internal:9099/hooks` | use when **finagent runs in Docker** and the inbox on your host |

## See why it happened

```bash
finagent explain <run_id>                         # or GET /runs/{run_id}/reasoning
```

This prints every key step with **who** decided (system, a skill, the model, or a human) and **why**, including refused approval attempts and their reasons.

## Notes

- The signing secret is read from `FINAGENT__WEBHOOKS__SIGNING_SECRET` (env or `.env`), otherwise from `config/settings.yaml`. It must match the service's secret.
- A rejection must include a reason (`comment`, at least 5 characters). The inbox fills one in.
