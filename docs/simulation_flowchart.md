# Wildfire simulation — one-page flowchart

**How to use:** Copy the first code block into [mermaid.live](https://mermaid.live) → export **SVG** or **PNG** → paste on one slide (use **landscape**).

---

## Full diagram (assumptions + pipeline + output)

```mermaid
flowchart TB
    subgraph LEGEND["Assumptions (what we hold fixed or configure per run)"]
        direction TB
        L0["Scene: 2 sensor UAVs to 1 ground controller · star topology"]
        L1["Flight: lawnmower patrol · thermal lower Z · imagery higher Z · same XY track"]
        L2["Fire: cellular automaton grid · shared fire_seed · advance by wall-clock step"]
        L3["Streams: 2 Hz · TCP JSON lines · ports 5001 thermal / 5002 imagery"]
        L4["Network: Mininet per-link delay + loss · constant within a run"]
        L5["Optional stress: app drop · dist_drop slope · clock offset or jitter per drone"]
        L6["Fusion policy fixed: pair window 2s · raw pair dt 0.5s · 2 confirmations in last 5 events"]
    end

    START([Start experiment]) --> CFG[Configure sweep: delay, loss, clock, dist_drop]
    CFG --> MN[Mininet: h1 controller · h2 thermal · h3 imagery]

    MN --> FIRE[Shared FireGrid: same RNG seed · both advance to same wall-clock step]

    FIRE --> TICK{Each 0.5 s tick}

    TICK --> TW[Thermal worker h2]
    TICK --> IW[Imagery worker h3]

    TW --> T1[Position: lawnmower + GPS noise]
    T1 --> T2[Thermal frame: grid or line · hotspot if fire visible]
    T2 --> T3[tx_ns + optional clock offset or jitter]
    T3 --> T4{Drop roll?}
    T4 -->|fail| TICK
    T4 -->|pass| T5[TCP 5001 to controller]

    IW --> I1[Position: same lawnmower XY · higher altitude]
    I1 --> I2[Detections: boxes · labels · empty frames]
    I2 --> I3[tx_ns + optional clock offset or jitter]
    I3 --> I4{Drop roll?}
    I4 -->|fail| TICK
    I4 -->|pass| I5[TCP 5002 to controller]

    T5 --> CTRL[Controller h1]
    I5 --> CTRL

    CTRL --> C1[Buffers · pair min thermal or imagery tx within 2 s]
    C1 --> C2[Raw signal: Tmax 100C and fire label and pair dt 0.5s]
    C2 --> C3[Rolling window: 2 of last 5 events confirm]
    C3 --> C4[Write latency_log.jsonl and fusion_log.csv]

    C4 --> E2E["E2E = t_fusion minus min of thermal tx and imagery tx"]
    E2E --> OUT[Outputs: per-run plots · compare_seeds aggregates]

    OUT --> ENDD([Next experiment or end])

    style LEGEND fill:#f5f5f5,stroke:#999
    style START fill:#e8f4e8
    style ENDD fill:#e8f4e8
    style E2E fill:#fff4e6
```

---

## Compact one-slide variant (single column)

```mermaid
flowchart TB
    subgraph LEGEND["Assumptions"]
        L0["2 UAVs + ground · lawn mower · CA fire · 2 Hz · Mininet per-link"]
    end

    START([Run]) --> CFG[Configure] --> MN[Mininet h1 h2 h3]
    MN --> FIRE[FireGrid sync]
    FIRE --> PAR[Parallel ticks]

    subgraph PAR["Each tick"]
        direction LR
        T[thermal: sample then tx drop then TCP 5001]
        I[imagery: detect then tx drop then TCP 5002]
    end

    PAR --> CTRL[Controller: pair then raw then rolling 2 of 5 then log]
    CTRL --> E2E[E2E latency breakdown plus TP FP FN TN]
```

---

## Draw.io / Lucidchart (ASCII wireframe)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ ASSUMPTIONS: 2 UAVs + ground │ lawn mower │ CA fire+seed │ 2Hz │ Mininet   │
└─────────────────────────────────────────────────────────────────────────┘
                                      │
                    ┌─────────────────┴─────────────────┐
                    ▼                                   ▼
            ┌───────────────┐                   ┌───────────────┐
            │ THERMAL h2    │                   │ IMAGERY h3    │
            │ fire grid→    │                   │ fire grid→    │
            │ sample→tx→drop│                   │ detect→tx→drop│
            │ → TCP 5001    │                   │ → TCP 5002    │
            └───────┬───────┘                   └───────┬───────┘
                    │                                   │
                    └──────────────┬────────────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │ CONTROLLER h1   pair |Δtx|≤2s │
                    │ raw → rolling 2/5 → log      │
                    └──────────────┬───────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │ E2E = t_fusion − min(tx)     │
                    │ + TP/FP/FN/TN                │
                    └──────────────────────────────┘
```
