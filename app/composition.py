from dataclasses import dataclass

from infrastructure.model_gateway import ModelGateway


@dataclass(frozen=True)
class AppServices:
    model_gateway: ModelGateway


def build_services(database, settings) -> AppServices:
    return AppServices(
        model_gateway=ModelGateway(database, settings),
    )