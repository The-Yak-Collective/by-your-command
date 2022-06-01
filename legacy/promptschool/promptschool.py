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
LISTOFTABLES=['prompts','hints','courses','responses']

load_dotenv(USER_DIR+'.env')

conn=sqlite3.connect(HOME_DIR+'promptschooldatabase.db')  
db_c = conn.cursor()

class standardrecord:
#based on db_c.execute('''CREATE TABLE {0} (seq INTEGER PRIMARY KEY, id int, creatorid text, contents text, filled int, createdat int, filledat int, parentid int, mlink text, other text)'''.format(tab)) 
    def __init__(self):
        self.seq=None
        self.id=0 #id of teh item, usually provided by discord
        self.creatorid=0 #user id of creator
        self.contents="" #the actual payload
        self.filled=0 #some sort of marking
        self.createdat=0 #when was it created
        self.filledat=0 #when was it marked
        self.parentid=0 # one level up in hierarchy
        self.mlink="" #some link data. not used
        self.other="" #some other data. not used

    def set(self, rawrecord):
        self.seq=rawrecord[0] #just a running count
        self.id=rawrecord[1] #id of teh item, usually provided by discord
        self.creatorid=rawrecord[2] #user id of creator
        self.contents=rawrecord[3] #the actual payload
        self.filled=rawrecord[4] #soem sort of marking
        self.createdat=rawrecord[5] #when was it created
        self.filledat=rawrecord[6] #when was it marked
        self.parentid=rawrecord[7] # one level up in hierarchy
        self.mlink=rawrecord[8] #some link data. not used
        self.other=rawrecord[9] #some other data. not used
        return (self)
    def totuple(self):
        return(self.seq, self.id, self.creatorid,self.contents,self.filled,self.createdat,self.filledat,self.parentid,self.mlink,self.other)

def putrecord(tab, rec):
    rec.createdat=int(time.time())
    rec.seq=None
    db_c.execute('''insert into {} values    (?,?,?,?,?,?,?,?,?,?)'''.format(tab),rec.totuple())
    conn.commit()
def getonerecord(tab, id):
    one=standardrecord()
    res=db_c.execute('select * from {} where id=? order by seq desc'.format(tab),(id,)).fetchone() #before we only returned contents.
    print(id,res)
    return one.set(res)
def getallrecords(tab, id):
    rows=db_c.execute('select * from {} where id=? order by seq desc'.format(tab),(id,)).fetchall()
    man=[]
    for r in rows:
        one=standardrecord()
        one.set(r)
        man.append(one)
    return(man)
def getqallrecords(tab, id=None,parentid=None,creatorid=None):
    wstr="1"
    if id:
        wstr=wstr+" and id="+str(id)
    if parentid:
        wstr=wstr+" and parentid="+str(parentid)
    if creatorid:
        wstr=wstr+" and creatorid="+str(creatorid)
    
    rows= db_c.execute('select * from {0} where {1} order by seq desc'.format(tab,wstr)).fetchall()
    man=[]
    for r in rows:
        one=standardrecord()
        one.set(r)
        man.append(one)
    return(man)
def getqonerecord(tab, id=None,parentid=None,creatorid=None):
    wstr="1"
    if id:
        wstr=wstr+" and id="+str(id)
    if parentid:
        wstr=wstr+" and parentid="+str(parentid)
    if creatorid:
        wstr=wstr+" and creatorid="+str(creatorid)
    one=standardrecord()
    return one.set(db_c.execute('select * from {0} where {1} order by seq desc'.format(tab,wstr)).fetchone())


@tree.command(description="set or replace a prompt for ongoing discussions in this thread")
@app_commands.describe(theprompt='text of prompt')
async def psset(interaction: discord.Interaction, theprompt: str):
    one=standardrecord()
    one.contents=theprompt
    one.creatorid=interaction.user.id
    one.id=interaction.channel_id
    putrecord("prompts",one)
    await interaction.response.send_message("hope you like your prompt!", ephemeral=True)
    return


@tree.command( description="show the current prompt of this for ongoing discussions")
async def psshow(interaction: discord.Interaction):
    try:
        one=getonerecord("prompts",interaction.channel_id)
        print("got one:",one)
        output=one.contents + str(list(one.totuple()))
    except:
        output="could not obtain prompt"
    await splitsend(interaction.channel,output,False)
    await interaction.response.send_message("done: "+output, ephemeral=True)
    return

@tree.command( description="ephemeral reminder of the current prompt of this for ongoing discussions")
async def psrecall(interaction: discord.Interaction):
    await interaction.response.send_message("support suspended", ephemeral=True)
    return
    try:
        rows=db_c.execute('select contents from prompts where chan=? order by  promptid desc',(interaction.channel_id,)).fetchone()
    except:
        rows=["could not obtain prompt"]
    if not rows:
        rows=["are you sure you created a prompt?"]
    await interaction.response.send_message("the prompt:\n"+rows[0], ephemeral=True)
    return
    
class test(app_commands.Group):
    @app_commands.command(name="echo")
    @app_commands.describe(name="the text")
    async test_echo(self,interaction:discord.Interaction,txt:str))
        await interaction.response.send_message(f'test echo {txt=}', ephemeral=True)
    @app_commands.command(name="doubleecho")
    @app_commands.describe(name="the text")
    async test_echo(self,interaction:discord.Interaction,txt:str))
        await interaction.response.send_message(f'test doubleecho {txt=}{txt=}', ephemeral=True)


@client.event #needed since it takes time to connect to discord
async def on_ready(): 
#    tree.copy_global_to(guild=client.guilds[0])
#    m= await tree.sync()
    tree.add_command(test)#need to be added manually for some reason
    tree.copy_global_to(guild=client.guilds[0]) #the commands were probably defined as global
    print(client.guilds[0],client.guilds[0].id)
    m= await tree.sync(guild=client.guilds[0])
    print([x.name for x in m])
    if len(m)==0:
        m= await tree.sync(guild=discord.Object(id=MY_GUILD_ID))
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
    for tab in LISTOFTABLES:
        db_c.execute('''SELECT count(name) FROM sqlite_master WHERE type='table' AND name=? ''',(tab,))
        if db_c.fetchone()[0]!=1:
            db_c.execute('''CREATE TABLE {0} (seq INTEGER PRIMARY KEY, id int, creatorid text, contents text, filled int, createdat int, filledat int, parentid int, mlink text, other text)'''.format(tab)) 
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
MY_GUILD_ID=os.getenv('THEYAKCOLLECTIVE_DISCORD_ID')
client.run(discord_token) 
