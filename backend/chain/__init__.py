"""온체인 레이어.

Solana devnet 과 직접 대화하는 유일한 계층이다. 이 패키지 바깥(services/, routers/)은
raw 정수 단위를 절대 보지 않으며, 모든 금액은 float USDC 단위로만 주고받는다.

패키지 이름이 `solana` 가 아닌 `chain` 인 이유:
    backend/ 가 sys.path 에 올라가 있어서 `backend/solana/` 는 solana-py 라이브러리의
    최상위 `solana` 패키지와 정면으로 충돌한다. __init__.py 가 있으면 라이브러리가
    가려지고(`from solana.rpc.async_api import ...` 실패), 없으면 이 디렉터리가
    import 불가능해진다. 두 경우 모두 치명적이라 이름을 분리했다.
"""
