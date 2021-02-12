#seperate files so we can split up robot between multiple files, if we want

import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.reactions = True

bot = commands.Bot(command_prefix='$$', intents=intents)
print('bot:',bot)



#client = discord.Client()#intents=intents)
