from abc import ABC, abstractmethod

from app.schemas.intelligence import AddressProfile, Transfer


class ChainAdapter(ABC):
    @abstractmethod
    async def get_tx(self, tx_hash: str) -> list[Transfer]: ...

    @abstractmethod
    async def get_transfers(
        self, address: str, direction="out", time_from=None, time_to=None
    ) -> list[Transfer]: ...

    @abstractmethod
    async def get_profile(self, address: str) -> AddressProfile: ...

    @abstractmethod
    async def get_balance(self, address: str, token=None) -> dict: ...
