## parts from here: https://gist.github.com/Rapptz/c4324f17a80c94776832430007ad40e6#slash-commands-and-context-menu-commands

    



from discord.ext import tasks, commands
import discord
import asyncio
import os
import re
import subprocess
import time
import datetime
from dotenv import load_dotenv

import sqlite3
import logging


from discord_promptschool import * #including client and tree

HOME_DIR="/home/yak/robot/promptschool/"
USER_DIR="/home/yak/"

load_dotenv(USER_DIR+'.env')

conn=sqlite3.connect(HOME_DIR+'promptschooldatabase.db') #the connection should be global. 


db_c = conn.cursor()



@tree.command(description="set a prompt for ongoing discussions")
@app_commands.describe(theprompt='text of prompt')
async def psset(interaction: discord.Interaction, theprompt: str):
    conts=theprompt
    db_c.execute('''insert into prompts values (NULL,?,?,?,?,?,?,?)''',(str(interaction.user.id),conts,0,int(time.time()),0,interaction.channel_id,"not in use"))
    conn.commit()
    await interaction.response.send_message("hope you like your prompt!", ephemeral=True)
    return

@tree.command( description="private reminder of the current prompt of this for ongoing discussions")
async def psrecall(interaction: discord.Interaction):
    try:
        rows=db_c.execute('select contents from prompts where chan=? order by  promptid desc',(interaction.channel_id,)).fetchone()
    except:
        rows=["could not obtain prompt"]
    if not rows:
        rows=["are you sure you created a prompt?"]
    await interaction.response.send_message("the prompt:\n"+rows[0], ephemeral=True)
    return

@tree.command( description="show the current prompt of this for ongoing discussions")
async def psshow(interaction: discord.Interaction):
    try:
        rows=db_c.execute('select contents from prompts where chan=? order by promptid desc',(interaction.channel_id,)).fetchone()
    except:
        rows=["could not obtain prompt"]
    if not rows:
        rows=["are you sure you created a prompt?"]
    await splitsend(interaction.channel,rows[0],False)
    await interaction.response.send_message("done", ephemeral=True)
    return


@tree.command(description="a simple echo as a test")
@app_commands.describe(echome='text to echo')
async def pstest(interaction: discord.Interaction, echome: str):
    await interaction.response.send_message(f'{echome=}', ephemeral=True)


@client.event #needed since it takes time to connect to discord
async def on_ready(): 
#    tree.copy_global_to(guild=client.guilds[0])
#    m= await tree.sync()
    m= await tree.sync(guild=client.guilds[0])
    print([x.name for x in m])
    checkon_database()
    print("promptschool is up!")
    print([x.name for x in tree.get_commands()])
    return

async def durl2m(u): #needs to be redone for thread...
    print(u)
    url=u.split("/")
    url=list(reversed(url))
    print(url)
    c=client.guilds[0].get_channel_or_thread(int(url[1]))
    m=await c.fetch_message(int(url[0]))
    return m,url[1],c

def checkon_database(): 
#check if table exists in DB. if not, create it
#this function is RIPE for automation, which would also be carried over to "on message"
    db_c.execute('''SELECT count(name) FROM sqlite_master WHERE type='table' AND name='prompts' ''')
    if db_c.fetchone()[0]!=1:
        db_c.execute('''CREATE TABLE prompts (promptid INTEGER PRIMARY KEY, creatorid text, contents text, filled int, createdat int, filledat int, chan int, mlink text)''') 
        #filled=is it active
        #most items will not be used...
        conn.commit()


async def splitsend(ch,st,codeformat):
#send data in chunks smaller than 2k
#might it have a bug of dropping last space and last line?
    if len(st)<1900: #discord limit is 2k and we want some play)
        if codeformat:
            await ch.send('```'+st+'```')
        else:
            await ch.send(st)
    else:
        x=st.rfind('\n',0,1900)
        if codeformat:
            await ch.send('```'+st[0:x]+'```')
        else:
            await ch.send(st[0:x])
        await splitsend(ch,st[x+1:],codeformat)

discord_token=os.getenv('PROMPTSCHOOL_DISCORD_KEY')
client.run(discord_token) 
