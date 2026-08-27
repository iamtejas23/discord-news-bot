import os
import re
import sqlite3
from datetime import datetime


import pytz
import feedparser
import discord

from dateutil import parser
from dotenv import load_dotenv

UTC = pytz.UTC
IST = pytz.timezone('Asia/Kolkata')

from discord.ext import commands, tasks
from discord import app_commands

from html import unescape


load_dotenv()


TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])

DB = "/data/news.db"


NEWS_INTERVAL = 30


FEEDS = {

    "BBC": {
        "url":
        "https://feeds.bbci.co.uk/news/rss.xml",
        "category":
        "World"
    },

    "Times of India": {
        "url":
        "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
        "category":
        "India"
    },

    "AWS": {
        "url":
        "https://aws.amazon.com/blogs/aws/feed/",
        "category":
        "Cloud"
    },

    "Hacker News": {
        "url":
        "https://hnrss.org/frontpage",
        "category":
        "Technology"
    }

}


KEYWORDS = [

    "aws",
    "eks",
    "kubernetes",
    "terraform",
    "security",
    "vulnerability",
    "hack",
    "cloud"

]


intents = discord.Intents.none()
intents.guilds = True

bot = commands.Bot(
    command_prefix=None,
    intents=intents,
    help_command=None
)




def ist_time():
    now_utc = datetime.now(UTC)
    now_ist = now_utc.astimezone(IST)

    return now_ist.strftime(
        "%d-%m-%Y %I:%M:%S %p IST"
    )

# =====================
# DATABASE
# =====================


def init_db():

    conn = sqlite3.connect(DB)

    conn.execute("""

    CREATE TABLE IF NOT EXISTS articles(

        url TEXT PRIMARY KEY,

        source TEXT,

        title TEXT,

        summary TEXT,

        published TEXT,

        category TEXT,

        created TEXT

    )

    """)


    conn.commit()

    conn.close()



# =====================
# HELPERS
# =====================


def clean_text(text):

    if not text:
        return ""

    text = re.sub(
        "<.*?>",
        "",
        text
    )

    text = unescape(text)

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()



def format_date(value):

    if not value:
        return "Unknown"


    try:

        dt = parser.parse(value)

        ist = pytz.timezone(
            "Asia/Kolkata"
        )


        return dt.astimezone(
            ist
        ).strftime(
            "%d-%m-%Y %I:%M %p IST"
        )


    except:

        return value



def priority_news(title):

    title = title.lower()

    for k in KEYWORDS:

        if k in title:
            return True

    return False




# =====================
# NEWS ENGINE
# =====================


def get_news(
        source=None,
        topic=None,
        limit=10):


    conn = sqlite3.connect(DB)

    result=[]


    feeds=FEEDS


    if source and source != "All":

        feeds={
            source:
            FEEDS[source]
        }


    for name,data in feeds.items():


        feed=feedparser.parse(
            data["url"]
        )


        for item in feed.entries[:20]:


            url=item.get(
                "link"
            )


            if not url:
                continue



            exists=conn.execute(

                "SELECT 1 FROM articles WHERE url=?",

                (url,)

            ).fetchone()



            if exists:
                continue



            title=clean_text(
                item.get(
                    "title",
                    "No title"
                )
            )


            summary=clean_text(

                item.get(
                    "summary",
                    ""
                )

            )


            if topic:

                if topic.lower() not in (

                    title+" "+summary

                ).lower():

                    continue



            if len(summary)>800:

                summary=summary[:800]+"..."



            published=item.get(
                "published",
                ""
            )


            conn.execute(

            """

            INSERT INTO articles

            VALUES(?,?,?,?,?,?,?)

            """,

            (

            url,

            name,

            title,

            summary,

            published,

            data["category"],

            datetime.now(UTC).isoformat()

            )


            )


            result.append({

            "url":url,

            "source":name,

            "title":title,

            "summary":summary,

            "published":published,

            "category":data["category"]

            })


            if len(result)>=limit:

                break


        if len(result)>=limit:

            break



    conn.commit()

    conn.close()


    return result




# =====================
# DISCORD
# =====================


async def send_article(
        channel,
        article):


    title=article["title"]


    if priority_news(title):

        title="🚨 "+title



    embed=discord.Embed(

        title=title,

        url=article["url"],

        description=

        article["summary"],

        timestamp=datetime.now(UTC)

    )


    embed.add_field(

        name="📰 Source",

        value=article["source"]

    )


    embed.add_field(

        name="📂 Category",

        value=article["category"]

    )


    embed.add_field(

        name="📅 Published",

        value=format_date(
            article["published"]
        )

    )


    embed.set_footer(

        text="CodexBot News"

    )


    await channel.send(
        embed=embed
    )




async def publish_news():

    channel=bot.get_channel(
        CHANNEL_ID
    )


    if not channel:

        return



    articles=get_news(
        limit=5
    )


    for article in articles:

        await send_article(
            channel,
            article
        )





# =====================
# COMMANDS
# =====================


@bot.tree.command(
name="news",
description="Latest news"
)
async def news(
interaction:discord.Interaction,
topic:str=None):


    await interaction.response.defer()


    articles=get_news(
        topic=topic,
        limit=10
    )


    if not articles:

        await interaction.followup.send(
            "No new news found"
        )

        return



    for a in articles:

        await send_article(
            interaction.channel,
            a
        )




@bot.tree.command(
name="sources",
description="News sources"
)
async def sources(
interaction:discord.Interaction):


    await interaction.response.send_message(

    "\n".join(

    [

    f"📰 {x} - {y['category']}"

    for x,y in FEEDS.items()

    ]

    )

    )





@bot.tree.command(
name="health",
description="Bot health"
)
async def health(
interaction:discord.Interaction):


    await interaction.response.send_message(

    f"""

🤖 CodexBot

Status: ONLINE

Servers:
{len(bot.guilds)}

Time:
{ist_time()}

"""

    )




@bot.tree.command(
name="status",
description="Feed status"
)
async def status(
interaction:discord.Interaction):


    lines=[
        "📡 Feed Status"
    ]


    for name,data in FEEDS.items():

        feed=feedparser.parse(
            data["url"]
        )


        lines.append(

        f"✅ {name}: {len(feed.entries)}"

        )


    await interaction.response.send_message(

        "\n".join(lines)

    )




# =====================
# LOOP
# =====================


@tasks.loop(minutes=NEWS_INTERVAL)
async def news_loop():

    try:

        await publish_news()

    except Exception as e:

        print(e)




# =====================
# START
# =====================


@bot.event
async def on_ready():


    init_db()


    await bot.tree.sync()


    if not news_loop.is_running():

        news_loop.start()



    print(

    f"""

CodexBot Started

User:
{bot.user}

Time:
{ist_time()}

"""

    )



bot.run(TOKEN)