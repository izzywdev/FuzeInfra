# MongoDB Consuming-Repo Provisioning

## Pattern
Add an entry to `docker/mongo/init/<repo>.js` (idempotent) and PR it to FuzeInfra.
Set `<USER>_PASSWORD` env vars in the consuming repo's SealedSecret.

## Existing consumers
| Repo | DB | User | Role |
|------|----|------|------|
| FuzePlan (main app) | FuzePlan | fuzeplan_app | readWrite |
| FuzePlan (claude-runner) | FuzePlan | claude_runner_user | readWrite |

## Adding a new consumer
1. Add user to `docker/mongo/init/00-<repo>.js`
2. PR to FuzeInfra — on merge, the init script documents the intended state
3. On cluster rebuild, run: `mongosh -u admin -p $ADMIN_PW --authenticationDatabase admin docker/mongo/init/00-<repo>.js`
