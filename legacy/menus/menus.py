#discord bot to act as UI for projects based on jordans(?) idea that we have a message for each project
#use PROJECT_UI_DISCORD_KEY as an env variable - the key for discord bot. needs read/write permission to channels

#logic:
#when run, starts by:
#   deletes all messages in dashboard
#   creates new upcoming message
#   gets list of channels
#   creates new message for each channel based on channel name and "other data sources" if any. for now, none! later, use $proj interface?
#   message includes name, blank text) channel (with #) and role:
#   each "create" also creates a line in the database with channel id and corresponding message id. maybe a list in memory is enough?
#   read existing roles
#   create/match (same first 10 chars or whole thing) a role for each channel and add it to list
#   add three reactions to each message: join and leave and details
#ongoing:
#   if new channel created or deleted or name changed, reboot program
#   if emoji
#       if on a message id in list
#           send a dm to that person, with details, if asked
#           if join, add a role
#           if leave, remove role
#   other commands, for now only test

import discord
import yaml
import asyncio
import os
import time
import datetime
import emoji
import subprocess
import re, string


from dotenv import load_dotenv
from discord.ext import tasks, commands

from discord_menus import * #including bot

HOMEDIR="/home/yak"
LOCALDIR=HOMEDIR+"/robot/menus"
import sqlite3 #not used as of now

class Int_Mess:#probably dont need but later maybe when we create a message 
    def __init__(self, id=0,name=None,typ=None,mess_id=0,update=None,role=None,content=None,emoji=None,chan=None):
        self.id=id #ID of the entry; int
        self.name=name # name of teh project/message
        self.typ=typ #for now "upcoming" or "project"
        self.mess_id=mess_id #ID of message in discord; int
        self.update=update #function to call to update the message
        #reaction=reaction #function to call when a reaction is added (clicked)
        self.role=role # what role to add or remove
        self.content=content #contents of the  message; text
        self.emoji=emoji #list of tuples (emojies strings, function to call, emojized) to show at bottom of message. 
        self.chan=chan
    

entries=[]#array of Int_Mess

load_dotenv(HOMEDIR+"/"+'.env')
TWEAK_CHAN=705512721847681035 #temporary
HELP_CHAN=809718017427636286 #channel for help messages

tweak_chan=0
help_chan=0



@bot.event #needed since it takes time to connect to discord
async def on_ready(): 
    global tweak_chan,help_chan
    print('We have logged in as {0.user}'.format(bot),  bot.guilds)

    tweak_chan=bot.guilds[0].get_channel(TWEAK_CHAN)
    help_chan=bot.guilds[0].get_channel(HELP_CHAN)
    
    await init_bot() 
    return

async def init_bot():
    global entries
    await delete_all_messages(help_chan)
    with open(LOCALDIR+"/menu.yaml") as f:
        tmp=f.read()
    entries=yaml.load(tmp)
    print("got:", entries)

    links={}
    for entry in entries:
        m=await create_message(entry)
        links[entry['entry']]=m
    for key in links:
        await swap_codes(links[key],links) #does message.edit

    
async def delete_all_messages(x): #for now, only bot messages
    def is_me(m):
        return m.author == bot.user
    deleted = await x.purge(limit=100, check=is_me)
    
async def create_message(e): #c is channel we are working on
    embed=discord.Embed(color=0xd12323)
    embed.add_field(name=e['title'], value=e['contents'], inline=False)
    return await help_chan.send(embed=embed)

async def swap_codes(m,links):
    em=m.embeds[0] # for now we support only a single embed
    thefield=em.fields[0]
    print("the embeds:",m.embeds[0],m.embeds[0].fields[0],m.embeds[0].fields[0].value)
    thevalue=thefield.value
    parts=thevalue.split('&<')
    print("the parts:",parts)
    for i,p in enumerate(parts):
        try:
            pos=p.index(">&")
            parts[i]=links[p[:pos-1]]+p[pos+2]
        except:
            pass
    thenewvalue="".join(parts)
    em.set_field_at(0, name=thefield.name,value=thenewvalue)
    await m.edit(embed=em)

#@bot.event #seems event eats the events that command uses. but it is not really needed, either
# fix is to add await bot.process_commands(message) at the end

@bot.command(name='menustest', help='a test response')
async def project_uitest(ctx):
    s='this is a test response from menus bot in bot mode'
    print('got here')
    await splitsend(ctx.message.channel,s,False)
    return
    
@bot.command(name='uploadmenu', help='upload a menu file')
async def uploadmenu(ctx):
    ctx.send("for now not implemented")


async def dmchan(t):
#create DM channel betwen bot and user
    target=bot.get_user(t)
    if (not target): 
        print("unable to find user and create dm",flush=True)
    return target
    target=target.dm_channel
    if (not target): 
        print("need to create dm channel",flush=True)
        target=await bot.get_user(t).create_dm()
    return target

async def splitsend(ch,st,codeformat):
#send messages within discord limit + optional code-type formatting
    if len(st)<1900: #discord limit is 2k and we want some play)
        if codeformat:
            return await ch.send('```'+st+'```')
        else:
            return await ch.send(st)
    else:
        x=st.rfind('\n',0,1900)
        if (x<0):
            x=1900
        if codeformat:
            return await ch.send('```'+st[0:x]+'```')
        else:
            return await ch.send(st[0:x])
        return await splitsend(ch,st[x+1:],codeformat)
        
#probbaly change to a "is_check()" type function
def allowed(x,y): #is x allowed to play with item created by y
#permissions - some activities can only be done by yakshaver, etc. or by person who initiated action
    if x==y: #same person. setting one to zero will force role check
        return True
    mid=bot.guilds[0].get_member(message.author.id)
    r=[x.name for x in mid.roles]
    if 'yakshaver' in r or 'yakherder' in r: #for now, both roles are same permissions
        return True
    return False

discord_token=os.getenv('PROJECT_UI_DISCORD_KEY')#later maybe change token
bot.run(discord_token) 
