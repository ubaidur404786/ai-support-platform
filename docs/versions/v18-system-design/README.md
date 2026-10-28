# v18 System design lab

Which part must be copied when users grow, and why? A small lab answers this with nginx, gateways, services and PostgreSQL. Every container has 1 CPU, and every change is measured.

![v18 platform design](../../architecture/v18.svg)

- Lab, commands, charts and all diagrams: [system-design/README.md](../../../system-design/README.md)
- Full write-up: [v18-system-design.md](../v18-system-design.md)
- Decision: [ADR-040](../../adr/ADR-040-scale-the-bottleneck.md)

```bash
cd system-design
docker compose up -d --build
sh run_experiments.sh && python plot_results.py
```
