from utils import wiki_operations


class BotState:
    """Holds shared mutable state for the bot instance."""

    def __init__(self) -> None:
        self.watchdog_last_tick = 0.0
        self.wikiops = wiki_operations.WikiOperations()


bot_state = BotState()
