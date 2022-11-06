#seperate files so we can split up robot between multiple files, if we want

import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.reactions = True
intents.members = True
intents.messages=True
intents.message_content=True #and now also to read messages...
bot = commands.Bot(command_prefix='$$', intents=intents)
print("---\n[" + datetime.datetime.now().astimezone().replace(microsecond=0).isoformat() + "]\nbot:",bot)



#client = discord.Client()#intents=intents)
