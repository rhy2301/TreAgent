# TreAgent Dashboard

React + TypeScript + Vite로 만든 TreAgent 대시보드입니다.

판단 엔진이 만든 시나리오 스냅샷(`public/snapshot.<scenario>.json`)을 읽어
지갑 잔고, AI 판단 근거, PolicyGuard 검사 결과, 집행 트랜잭션을 보여줍니다.

```bash
npm install
npm run dev     # http://localhost:5173
```

시나리오는 `?scenario=normal_shortfall | prompt_injection | inference_budget_low` 로 바꿀 수 있습니다.
스냅샷을 새로 만드는 방법은 [docs/SETUP.md](../docs/SETUP.md#프론트엔드-실행)를 참고하세요.
