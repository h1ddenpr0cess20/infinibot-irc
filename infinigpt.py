import asyncio
import logging
import httpx
import textwrap
import json
import time
from irc.bot import SingleServerIRCBot

class InfiniGPT(SingleServerIRCBot):
    """
    An asynchronous IRC bot integrated with multiple LLM APIs.

    Features:
    - Supports OpenAI, xAI, Google, and Ollama models for generating responses.
    - Manages user and channel-specific conversational history.
    - Provides dynamic model switching, persona customization, and message handling.
    - Responds to commands for AI interaction, resetting settings, and help menus.

    Configuration:
    Reads settings from a JSON file, including IRC credentials, LLM API keys, 
    and default behavior settings.
    
    Attributes:
        server (str): IRC server to connect to.
        nickname (str): Bot's nickname on the IRC server.
        password (str): Password for NickServ identification.
        _channels (list): List of channels to join.
        admin (str): Nickname of the bot's admin.
        models (dict): Supported LLM models grouped by provider.
        api_keys (dict): API keys for different providers.
        default_model (str): Default model for generating responses.
        default_personality (str): Default personality for the bot.
        prompt (list): System prompt template for LLM interactions.
        options (dict): Additional options for API calls (currently not implemented).
        messages (dict): Tracks conversation history per channel and user.
    """
    def __init__(self, port=6667):
        """
        Initialize the InfiniGPT bot and load configurations.

        Args:
            port (int, optional): Port to connect to on the IRC server. Defaults to 6667.
        """
        with open("config.json", "r") as f:
            self.config = json.load(f)
            f.close()

        self.server, self.nickname, self.password, self._channels, self.admin = self.config["irc"].values()
        self.models, self.api_keys, self.default_model, self.default_personality, self.prompt, self.options = self.config["llm"].values()
        self.openai_key, self.xai_key, self.google_key = self.api_keys.values()
        self.personality = self.default_personality
        self.messages = {}

        super().__init__([(self.server, port)], self.nickname, self.nickname)        

    async def respond(self, sender, messages, sender2=False):
        """
        Generate a response using the configured LLM.

        Args:
            sender (str): Nickname of the sender.
            messages (list): Message history to provide as context.
            sender2 (str, optional): Alternative sender name for response tagging.

        Returns:
            tuple: The name for response attribution and a list of response lines.
        """
        if self.model in self.models["openai"]:
            bearer = self.openai_key
            self.url = "https://api.openai.com/v1"
        elif self.model in self.models["xai"]:
            bearer = self.xai_key
            self.url = "https://api.x.ai/v1"
        elif self.model in self.models["google"]:
            bearer = self.google_key
            self.url = "https://generativelanguage.googleapis.com/v1beta/openai"
        elif self.model in self.models["ollama"]:
            bearer = "hello_friend"
            self.url = "http://localhost:11434/v1"

        headers = {
                "Authorization": f"Bearer {bearer}",
                "Content-Type": "application/json"
        }
        data = {
            "model": self.model,
            "messages": messages
        }

        async with httpx.AsyncClient() as client:
            url = f"{self.url}/chat/completions"
            response = await client.post(url=url, headers=headers, json=data, timeout=180)
            response.raise_for_status()
            result = response.json()
        name = sender2 if sender2 else sender
        lines = self.chop(result['choices'][0]['message']['content'])
        return name, lines
    
    def change_model(self, connection, channel=None, model=None, sender=None):
        """
        Change the active LLM model.

        Args:
            connection (IRCConnection): IRC connection instance.
            channel (str, optional): Channel to send feedback messages to.
            model (str, optional): Desired model to switch to.
        """
        if model != None:
            for provider, models in self.models.items():
                if model in models:
                    self.model = model
                    logger.info(f"Model set to {self.model}")
                    if channel != None:
                        connection.privmsg(channel if channel != "privmsg" else sender, f"Model set to {self.model}")
                    return
            if channel != None:
                connection.privmsg(channel if channel != "privmsg" else sender, f"Model {model} not found in available models.")
        else:
            if channel != None:
                current_model = [
                    f"Current model: {self.model}",
                    "Available models: " + ", ".join(
                        [model for provider, models in self.models.items() for model in models]
                    )
                ]
                for line in current_model:
                    connection.privmsg(channel if channel != "privmsg" else sender, line)

    def on_welcome(self, connection, event):
        """
        Handle server welcome event and join configured channels.

        Args:
            connection (IRCConnection): IRC connection instance.
            event (IRCEvent): Event details from the server.
        """
        logger.info(f"Connected to {self.server}")
        if self.password != None:
            connection.privmsg("NickServ", f"IDENTIFY {self.password}")
            logger.info("Identifying to NickServ")
            time.sleep(5)
        self.change_model(connection, model=self.default_model)
        system_prompt = self.prompt[0] + self.default_personality + self.prompt[1]
        logger.info(f"System prompt set to {system_prompt}")
        future = asyncio.run_coroutine_threadsafe(
            self.respond(sender=None, messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": "introduce yourself"}]),
            self.loop
        )
        name, lines = future.result()
        lines.append("Type .help to learn how to use me.")
        for channel in self._channels:
            logger.info(f"Joining channel: {channel}")
            connection.join(channel)
            
            logger.info(f"Sending response to {channel}: {' '.join(lines)}")
            for line in lines:
                connection.privmsg(channel, line)
                asyncio.run_coroutine_threadsafe(asyncio.sleep(1.5), self.loop)
    
    def on_join(self, connection, event):
        """Actions to take when a user joins.  Currently not implemented."""
        pass

    def on_nicknameinuse(self, connection, event):
        """
        Handle nickname-in-use errors by appending an underscore.

        Args:
            connection (IRCConnection): IRC connection instance.
            event (IRCEvent): Event details from the server.
        """
        connection.nick(connection.get_nickname() + "_")

    def on_privmsg(self, connection, event):
        """
        Privately chat with the bot, without having to use .ai command
        
        Args:
            connection (IRCConnection): IRC connection instance.
            event (IRCEvent): Event details from the server.
        """
        sender = event.source.nick
        message = event.arguments[0].split(" ")
        if "privmsg" not in self.messages:
            self.messages['privmsg'] = {}
        if sender not in self.messages['privmsg']:
            self.messages['privmsg'][sender] = []
            self.messages['privmsg'][sender].append({"role": "system", "content": self.prompt[0] + self.default_personality + self.prompt[1]})

        if sender != self.nickname:
            asyncio.run_coroutine_threadsafe(self.handle_privmsg(connection, sender, message), self.loop)

    def on_pubmsg(self, connection, event):
        """
        Handle public messages and trigger message processing.

        Args:
            connection (IRCConnection): IRC connection instance.
            event (IRCEvent): Event details from the server.
        """
        channel = event.target
        sender = event.source.nick
        message = event.arguments[0].split(" ")

        if sender != self.nickname:
            asyncio.run_coroutine_threadsafe(self.handle_message(connection, channel, sender, message), self.loop)

    def chop(self, message):
        """
        Break a message into lines of at most 420 characters, preserving whitespace.

        Args:
            message (str): The message to be chopped.

        Returns:
            list: Lines of the message within the 420-character limit.
        """
        lines = message.splitlines()
        newlines = []  

        for line in lines:
            if len(line) > 420:
                wrapped_lines = textwrap.wrap(
                    line,
                    width=420,
                    drop_whitespace=False,
                    replace_whitespace=False,
                    fix_sentence_endings=True,
                    break_long_words=False)
                newlines.extend(wrapped_lines) 
            else:
                newlines.append(line) 
        return newlines  

    async def add_history(self, role, channel, sender, message, default=True):
        """
        Add a message to the conversation history.

        Args:
            role (str): Role of the message sender ("system", "user", "assistant").
            channel (str): Channel where the message occurred.
            sender (str): Nickname of the message sender.
            message (str): Content of the message.
            default (bool, optional): Whether to add the default system prompt.
        """
        if channel not in self.messages:
            self.messages[channel] = {}
        if sender not in self.messages[channel]:
            self.messages[channel][sender] = []
            if default:
                self.messages[channel][sender].append({"role": "system", "content": self.prompt[0] + self.default_personality + self.prompt[1]})
        self.messages[channel][sender].append({"role": role, "content": message})

        if len(self.messages[channel][sender]) > 24:
            if self.messages[channel][sender][0]["role"] == "system":
                del self.messages[channel][sender][1:3]
            else:
                del self.messages[channel][sender][0:2]

    async def ai(self, connection, channel, sender, message, x=False):
        """
        Process user requests and generate LLM responses.

        Args:
            connection (IRCConnection): IRC connection instance.
            channel (str): Channel where the message was sent.
            sender (str): Nickname of the sender.
            message (list): Parsed user message as a list of words.
            x (bool, optional): Whether the message is directed to another user.
        """
        if x and message[2]:
            target = message[1]
            message = ' '.join(message[2:])
            if target in self.messages[channel]:
                await self.add_history("user", channel, target, message)
                name, lines = await self.respond(target, self.messages[channel][target], sender2=sender)
                await self.add_history("assistant", channel, target, ' '.join(lines))
            else:
                pass
        else:
            message = ' '.join(message[1:])
            await self.add_history("user", channel, sender, message)
            name, lines = await self.respond(sender, self.messages[channel][sender])
            await self.add_history("assistant", channel, name, ' '.join(lines))

        logger.info(f"Sending response to {name} in {channel}: {' '.join(lines)}")
        connection.privmsg(channel, f"{name}:")
        await asyncio.sleep(1.5)
        for line in lines:
            connection.privmsg(channel, line)
            await asyncio.sleep(1.5)
    
    async def set_prompt(self, connection, channel, sender, persona=None, custom=None, respond=True):
        """
        Set a custom or predefined system prompt.

        Args:
            connection (IRCConnection): IRC connection instance.
            channel (str): Channel where the prompt is being set.
            sender (str): Nickname of the sender.
            persona (str, optional): Predefined personality for the prompt.
            custom (str, optional): Custom text for the system prompt.
            respond (bool, optional): Whether to introduce the bot after setting.
        """
        if channel in self.messages:
            if sender in self.messages[channel]:
                self.messages[channel][sender].clear()
        if persona != None:
            system_prompt = self.prompt[0] + persona + self.prompt[1]
        elif custom != None:
            system_prompt = custom
        
        await self.add_history("system", channel, sender, system_prompt, default=False)
        logger.info(f"System prompt for {sender} set to {system_prompt}")
        
        if respond:
            await self.add_history("user", channel, sender, "introduce yourself")
            name, lines = await self.respond(sender, self.messages[channel][sender])
            await self.add_history("assistant", channel, name, ' '.join(lines))
            logger.info(f"Sending response to {name} in {channel}: {' '.join(lines)}")
            if channel != "privmsg":
                connection.privmsg(channel, f"{name}:")
                await asyncio.sleep(1.5)
            for line in lines:
                connection.privmsg(channel if channel != "privmsg" else sender, line)
                await asyncio.sleep(1.5)
            
    async def reset(self, connection, channel, sender, stock=False):
        """
        Reset the bot's conversation history for a user.

        Args:
            connection (IRCConnection): IRC connection instance.
            channel (str): Channel where the reset is initiated.
            sender (str): Nickname of the user to reset.
            stock (bool, optional): Whether to apply stock settings.
        """
        if channel in self.messages:
            if sender in self.messages[channel]:
                self.messages[channel][sender].clear()
        if not stock:
            await self.set_prompt(connection, channel, sender, persona=self.default_personality, respond=False)
            connection.privmsg(channel if channel != "privmsg" else sender, f"{self.nickname} reset to default for {sender}")
        else:
            connection.privmsg(channel if channel != "privmsg" else sender, f"Stock settings applied for {sender}")
    
    async def help_menu(self, connection, sender):
        """
        Display a help menu to the user.

        Args:
            connection (IRCConnection): IRC connection instance.
            sender (str): Nickname of the user requesting help.
        """
        with open("help.txt", "r") as f:
            help_text = f.readlines()
        for line in help_text:
            connection.notice(sender, line.strip())
            await asyncio.sleep(1.5)

    async def handle_message(self, connection, channel, sender, message):
        """
        Handle user messages and execute corresponding commands.

        Args:
            connection (IRCConnection): IRC connection instance.
            channel (str): Channel where the message was sent.
            sender (str): Nickname of the message sender.
            message (list): Parsed user message as a list of words.
        """
        user_commands = {
            ".ai": lambda: self.ai(connection, channel, sender, message),
            f"{self.nickname}:": lambda: self.ai(connection, channel, sender, message),
            f"{self.nickname},": lambda: self.ai(connection, channel, sender, message),
            ".x": lambda: self.ai(connection, channel, sender, message, x=True),
            ".persona": lambda: self.set_prompt(connection, channel, sender, persona=' '.join(message[1:])),
            ".custom": lambda: self.set_prompt(connection, channel, sender, custom=' '.join(message[1:])),
            ".reset": lambda: self.reset(connection, channel, sender),
            ".stock": lambda: self.reset(connection, channel, sender, stock=True),
            ".help": lambda: self.help_menu(connection, sender)
        }
        admin_commands = {
            ".model": lambda: self.change_model(connection, channel, model=message[1] if len(message) > 1 else None)
        }

        command = message[0]
        if command in user_commands:
            logger.info(f"Received message from {sender} in {channel}: {' '.join(message)}")
            action = user_commands[command]
            await action()
        if sender == self.admin and command in admin_commands:
            logger.info(f"Received message from {sender} in {channel}: {' '.join(message)}")
            action = admin_commands[command]
            await action()

    async def handle_privmsg(self, connection, sender, message):
        """
        Handle private user messages and execute corresponding commands.

        Args:
            connection (IRCConnection): IRC connection instance.
            sender (str): Nickname of the message sender.
            message (list): Parsed user message as a list of words.
        """
        user_commands = {
            ".persona": lambda: self.set_prompt(connection, "privmsg", sender, persona=' '.join(message[1:])),
            ".custom": lambda: self.set_prompt(connection, "privmsg", sender, custom=' '.join(message[1:])),
            ".reset": lambda: self.reset(connection, "privmsg", sender),
            ".stock": lambda: self.reset(connection, "privmsg", sender, stock=True),
            ".help": lambda: self.help_menu(connection, sender)
        }
        admin_commands = {
            ".model": lambda: self.change_model(connection, "privmsg", model=message[1] if len(message) > 1 else None, sender=sender)
        }

        command = message[0]
        if command in user_commands:
            logger.info(f"Received private message from {sender}: {' '.join(message)}")
            action = user_commands[command]
            await action()
        elif sender == self.admin and command in admin_commands:
            logger.info(f"Received private message from {sender}: {' '.join(message)}")
            action = admin_commands[command]
            await action()
        else:
            await self.add_history("user", "privmsg", sender, ' '.join(message))
            logger.info(f"Received private message from {sender}: {' '.join(message)}")
            name, lines = await self.respond(sender, self.messages["privmsg"][sender])
            await self.add_history("assistant", "privmsg", sender, ' '.join(lines))
            logger.info(f"Sending response to {sender}: {' '.join(lines)}")
            for line in lines:
                connection.privmsg(sender, line)
                await asyncio.sleep(1.5)


async def start_bot():
    """
    Starts the InfiniGPT bot using the current event loop.
    """
    bot = InfiniGPT()
    bot.loop = asyncio.get_running_loop()
    await asyncio.get_event_loop().run_in_executor(None, bot.start)

def main():
    """
    Initializes and runs the InfiniGPT bot.
    """
    asyncio.run(start_bot())

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)
    main()

