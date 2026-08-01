from solders.keypair import Keypair
import base58

def create_wallet():
		"""새 지갑 생성"""
		kp = Keypair()
		private_key = base58.b58encode(bytes(kp)).decode()
		public_key = str(kp.pubkey())
		return {"public_key": public_key, "private_key": private_key}

if __name__ == "__main__":
		# 이 파일 직접 실행하면 지갑 3개 생성됨
		wallets = ["treasury_vault", "payroll_wallet", "operations_wallet"]
		for name in wallets:
				w = create_wallet()
				print(f"\n{name}:")
				print(f"  Public:  {w['public_key']}")
				print(f"  Private: {w['private_key']}")
				print(f"  → .env에 PRIVATE_KEY로 저장하고, Public Key는 Devnet Faucet에서 SOL 받기")