from app.adapters.bitcoin import BitcoinAdapter
from app.adapters.evm import EvmAdapter
from app.adapters.synthetic import SyntheticAdapter
from app.adapters.tron import TronAdapter
from app.core.errors import AppError


class AdapterRegistry:
    def __init__(self, db, scenario_id=None):
        self.db, self.scenario_id = db, scenario_id
        self.instances = {}

    def get(self, chain):
        chain = getattr(chain, "value", chain)
        if chain not in {"tron", "ethereum", "bnb", "polygon", "bitcoin"}:
            raise AppError(
                "UNSUPPORTED_CHAIN", "This chain is not implemented", 422, {"chain": chain}
            )
        if chain not in self.instances:
            self.instances[chain] = (
                SyntheticAdapter(self.db, chain, self.scenario_id)
                if self.scenario_id
                else TronAdapter(self.db)
                if chain == "tron"
                else BitcoinAdapter(self.db)
                if chain == "bitcoin"
                else EvmAdapter(self.db, chain)
            )
        return self.instances[chain]
