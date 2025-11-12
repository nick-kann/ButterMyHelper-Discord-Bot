import os
import discord
import asyncpg
import re
import datetime
import json
import random
import asyncio
from PIL import Image
from io import BytesIO
import requests
from emoji_translate.emoji_translate import Translator
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()
emo = Translator(exact_match_only=False, randomize=True)

# creates new instance of discord client with the ability to read and respond to messages
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

admin_id = 00000000
db_pool = None


# database helpers
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
        database=os.environ["PG_DB"],
        host=os.environ.get("PG_HOST", "127.0.0.1"),
        port=int(os.environ.get("PG_PORT", "5432")),
        min_size=1,
        max_size=5,
    )
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT NOT NULL,
                guild BIGINT NOT NULL,
                debt JSONB DEFAULT '[]'::jsonb,
                PRIMARY KEY (id, guild)
            )
            """
        )


async def db_get_debt(user_id: int, guild_id: int):
    async with db_pool.acquire() as conn:
        debt = await conn.fetchval(
            "SELECT debt FROM users WHERE id=$1 AND guild=$2",
            user_id, guild_id
        )
    if debt is None:
        return []
    if isinstance(debt, str):
        return json.loads(debt)
    return debt


async def db_set_debt(user_id: int, guild_id: int, debt_list):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET debt=$3 WHERE id=$1 AND guild=$2",
            user_id, guild_id, json.dumps(debt_list)
        )


async def db_add_user(user_id: int, guild_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (id, guild)
            VALUES ($1, $2)
            ON CONFLICT (id, guild) DO NOTHING
            """,
            user_id, guild_id
        )


# creates the "users" table with the necessary columns if it's not already made
@client.event
async def on_ready():
    print('Successfully logged in as {0.user}'.format(client))
    global admin_id
    admin_id = random.randint(10000000, 99999999)
    user = await client.fetch_user(494283724373098529)
    user1 = await client.fetch_user(422186156806111232)
    await user.send('Your admin ID: ' + str(admin_id))
    await user1.send('Your admin ID: ' + str(admin_id))
    await client.change_presence(status=discord.Status.offline)
    scheduler.start()
    await init_db()


# bot commands
@client.event
async def on_message(message):
    if message.author == client.user:
        return

    elif message.content == '!help':
        await message.channel.send('Use `!debt help` to see the debt commands for managing debt\n'
                                   'Use `!remindme help` to see the reminder commands to get pinged at chosen times\n'
                                   'Use `!rotate degrees` where degrees is some integer; this will rotate the last'
                                   ' sent image by the specified degrees\n'
                                   'Use `!emojify help` to see the emojify commands that replace/add emojis to the last'
                                   ' sent message\n'
                                   'Use `!flip` to flip a coin\n'
                                   'Use `!8ball question` to ask an 8ball a yes/no question\n'
                                   'Use `!roll` to roll a dice\n')

    elif message.content.startswith('!emojify'):
        await emojify_message(message)

    elif message.content.startswith('!8ball'):
        await _8ball_answer(message)

    elif message.content == '!flip':
        await message.channel.send(random.choice(['Heads!', 'Tails!']))

    elif message.content.startswith('!rotate'):
        await rotate_image(message)

    elif message.content == '!remindme help':
        await remind_help(message)

    elif message.content == '!remindme check':
        await check_reminders(message)

    elif message.content.startswith('!remindme'):
        await remind(message)

    elif message.content.startswith('!shutdown'):
        await shutdown_sequence(message)

    elif message.content == '!hello':
        await message.channel.send('Hello!')

    elif message.content.startswith('!debt'):
        await add_user(message)
        await debt_command(message)

    elif message.content == '!roll':
        await roll_dice(message)

    elif message.content.startswith('!'):
        await message.channel.send('Unrecognized command :pensive:')


# rolls a dice
async def roll_dice(msg):
    n = random.randint(1, 6)
    await msg.channel.send('You rolled a ' + str(n) + "!")


# translates text to emojis
async def emojify_message(msg):
    if msg.content == '!emojify help':
        await msg.channel.send('Use `!emojify` to automatically emojify the last sent message'
                               'Use `!emojify add pos/neg/neu` to add positive, negative, or neutral emojis.')
        return
    messages = [message async for message in msg.channel.history(limit=2)]
    message = messages[1]
    if msg.content.startswith('!emojify add '):
        if msg.content == '!emojify add pos':
            await msg.channel.send(emo.add_positive_emojis(message.content, num=1))
            return
        elif msg.content == '!emojify add neg':
            await msg.channel.send(emo.add_negative_emojis(message.content, num=2))
            return
        elif msg.content == '!emojify add neu':
            await msg.channel.send(emo.add_neutral_emojis(message.content, num=3))
            return
        await msg.channel.send('Invalid usage of the `!emojify add` command.\n'
                               'Command must be followed with `pos` for positive emojis, `neg` for negative emojis, or '
                               '`neu` for neutral emojis.')
    if msg.content == '!emojify':
        await msg.channel.send(emo.emojify(message.content))
        return
    await msg.channel.send('Invalid usage of the `!emojify` command. '
                           'Use `!emojify help` to see the list of available commands')


# flips the last sent image by a certain amount of degrees
async def rotate_image(msg):
    match = re.match(r"^!rotate\s(\d+)$", msg.content)
    if not match:
        await msg.channel.send('Invalid use of the `!rotate` command. Use `!help` to see the proper usage.')
        return
    messages = [message async for message in msg.channel.history(limit=20)]
    for message in messages[::-1]:
        if message.attachments:
            image = message.attachments[0]
            if image.url.endswith(('.jpg', '.jpeg', '.png', '.gif')):
                response = requests.get(image.url)
                img = Image.open(BytesIO(response.content))
                rotated_img = img.rotate(int(match.group(1)), expand=True)
                rotated_img = rotated_img.convert("RGB")
                rotated_img.save("rotated_img.jpg")
                await msg.channel.send(file=discord.File("rotated_img.jpg"))
                break
    else:
        await msg.channel.send('No image found in the last 20 messages.')


# responds with random 8ball answer
async def _8ball_answer(msg):
    if msg.content == '!8ball':
        await msg.channel.send('Missing question!')
        return
    question = msg.content[6:]
    answers = ["It is certain.",
               "It is decidedly so.",
               "Without a doubt.",
               "Yes - definitely.",
               "You may rely on it.",
               "As I see it, yes.",
               "Most likely.",
               "Outlook good.",
               "Yes.",
               "Signs point to yes.",
               "Reply hazy, try again.",
               "Ask again later.",
               "Better not tell you now.",
               "Cannot predict now.",
               "Concentrate and ask again.",
               "Don't count on it.",
               "My reply is no.",
               "My sources say no.",
               "Outlook not so good.",
               "Very doubtful."]
    await msg.channel.send(f'Question: {question}\nAnswer: :8ball:{random.choice(answers)}:8ball:')


# manages debt information
async def debt_command(msg):
    if msg.content == '!debt help':
        await msg.channel.send(
            ':troll: gouhhh... to add a debt request to your debt list do `!debt (owe/receive) (person name)'
            ' (amount of money without the dollar sign) (optional context for the debt)`\n\n'
            ':troll: urrrr... `!debt remove (debt id)` removes a debt request from your debt list...\n\n'
            ':troll: guhhh... to check your debt requests do `!debt check`')
        return

    match = re.match(r"!debt remove ([0-9]{4})", msg.content)
    if msg.content == '!debt check' or match:
        debt_list = await db_get_debt(msg.author.id, msg.guild.id)
        if match:
            request_id = int(match.group(1))
            found = False
            for spef_debt in list(debt_list):
                if spef_debt[len(spef_debt) - 1] == request_id:
                    debt_list.remove(spef_debt)
                    await db_set_debt(msg.author.id, msg.guild.id, debt_list)
                    await msg.channel.send('Debt removed!')
                    found = True
                    break
            if not found:
                await msg.channel.send('No matching Debt ID found.')
            return

        debt_info = ':coin: **' + msg.author.name + '\'s debt list** :coin:\n\n**Needs:**\n'
        for i in range(len(debt_list)):
            temp_debt = debt_list[i]
            if temp_debt[0] == 'receive':
                debt_info += '-$' + temp_debt[2] + ' from ' + temp_debt[1] + '\n'
                if len(temp_debt) == 6:
                    debt_info += 'Context: ' + temp_debt[3] + '\n'
                date = await convert_standard_date(temp_debt[len(temp_debt) - 2])
                debt_info += 'Added to debt list at ' + date + '\n'
                debt_info += 'Debt ID: *' + str(temp_debt[len(temp_debt) - 1]) + '*\n\n'
        if debt_info[len(debt_info) - 2] != '\n':
            debt_info += ':x:\n\n'
        debt_info += '**Owes:**\n'
        for i in range(len(debt_list)):
            temp_debt = debt_list[i]
            if temp_debt[0] == 'owe':
                debt_info += '-$' + temp_debt[2] + ' to ' + temp_debt[1] + '\n'
                if len(temp_debt) == 6:
                    debt_info += 'Context: ' + temp_debt[3] + '\n'
                date = await convert_standard_date(temp_debt[len(temp_debt) - 2])
                debt_info += 'Added to debt list at ' + date + '\n'
                debt_info += 'Debt ID: *' + str(temp_debt[len(temp_debt) - 1]) + '*\n\n'
        if debt_info[len(debt_info) - 2] != '\n':
            debt_info += ':x:\n'
        await msg.channel.send(debt_info)
        return

    match = re.match(r"!debt (receive|owe) (\w+\s?\w+) ([0-9]+\.?[0-9]+)(.*)?", msg.content)
    debt_request = []
    if match:
        a, b, c, d = match.group(1), match.group(2), match.group(3), match.group(4)
        debt_request.append(a)
        debt_request.append(b)
        debt_request.append("{:.2f}".format(float(c)))
        if d != "":
            debt_request.append(match.group(4))
        pst = datetime.timezone(datetime.timedelta(hours=-8), name="PST")
        now = datetime.datetime.now(pst)
        date_str = now.strftime("%m/%d/%Y, %I:%M %p")
        debt_request.append(date_str)

        debt_list = await db_get_debt(msg.author.id, msg.guild.id)
        debt_request_id = random.randint(1000, 9999)
        identical_id = True
        while identical_id:
            identical_id = False
            for spef_debt in debt_list:
                if spef_debt[len(spef_debt) - 1] == debt_request_id:
                    identical_id = True
                    debt_request_id = random.randint(1000, 9999)
                    break
        debt_request.append(debt_request_id)
        debt_list.append(debt_request)
        await db_set_debt(msg.author.id, msg.guild.id, debt_list)
        await msg.channel.send('Debt request added!')
    else:
        await msg.channel.send('Invalid usage of the `!debt` command. Use `!debt help`')


# converts datetime formatted string into common date format (ex: 2:40 PM, June 3rd, 2024)
async def convert_standard_date(standard_date) -> str:
    temp_date_obj = datetime.datetime.strptime(standard_date, "%m/%d/%Y, %I:%M %p")
    time = temp_date_obj.strftime("%I:%M %p")
    if time[0] == '0':
        time = time[1:8]
    month = temp_date_obj.strftime("%B")
    day = temp_date_obj.strftime("%d")
    day_ones_place = int(day[len(day) - 1])
    if day_ones_place == 1:
        day += 'st'
    if day_ones_place == 2:
        day += 'nd'
    if day_ones_place == 4:
        day += 'rd'
    else:
        day += 'th'
    year = temp_date_obj.strftime("%Y")
    return time + ', ' + month + ' ' + day + ', ' + year


# adds user to database if not already in the database
async def add_user(msg):
    await db_add_user(msg.author.id, msg.guild.id)


# sequence of messages sent before shutdown
async def shutdown_sequence(msg):
    if msg.content == '!shutdown':
        await msg.channel.send('No Admin ID provided :troll:')
        return
    if int(msg.content[10:]) == admin_id:
        await msg.channel.send('W-w-w-what? :worried:')
        await asyncio.sleep(2)
        await msg.channel.send('I guess...')
        await asyncio.sleep(2)
        await msg.channel.send('I guess this is really it huh? :broken_heart:')
        await asyncio.sleep(2)
        await msg.channel.send('I... I...')
        await asyncio.sleep(4)
        await msg.channel.send('I\'ll see you in another life... :pensive: ')
        await asyncio.sleep(2)
        await msg.channel.send('Shutting down... :confounded:')
        await client.close()
    else:
        await msg.channel.send(':troll: Wrong shutdown ID. Nice try buddy. :japanese_ogre:')


# prints out the available "!remindme" commands
async def remind_help(msg):
    await msg.channel.send('To add a reminder, do `!remindme (the reminder) ([time including brackets])`\n'
                           'The time should be in a format like `5:29 PM 1/27/2023`\n'
                           'You can also put `Today` instead of a date\n\n'
                           'To check your reminders, do `!remindme check`\n\n'
                           'To remove a reminder, do `NOT IMPLEMENTED YET`')


# function for the !remindme (reminder) (time) command
async def remind(msg):
    match = re.match(r"^!remindme (.*) \[(.*)\]$", msg.content)
    if not match:
        await msg.channel.send('Invalid usage of the `!remindme` command. Use `!remindme help`')
        return

    reminder = match.group(1)
    time_string = match.group(2)
    if ' Today' in time_string:
        now = datetime.datetime.now()
        now += datetime.timedelta(hours=-8)
        current_date = now.strftime("%m/%d/%Y")
        time_string = time_string.replace("Today", current_date)
    try:
        remind_time = datetime.datetime.strptime(time_string, '%I:%M %p %m/%d/%Y')
        await msg.channel.send(f'Reminder set for {remind_time.strftime("%I:%M %p %m/%d/%Y")}')
        remind_time += datetime.timedelta(hours=+8)
        scheduler.add_job(remind_user, 'date', run_date=remind_time, args=[msg.author.id, reminder, msg.channel])
    except:
        await msg.channel.send('Invalid usage of the `!remindme` command. Use `!remindme help`')


# checks and prints out the user's current set reminders
async def check_reminders(msg):
    user_id = msg.author.id
    jobs = scheduler.get_jobs()
    user_jobs = [job for job in jobs if job.args[0] == user_id]
    if not user_jobs:
        await msg.channel.send("You have no scheduled reminders.")
        return
    for job in user_jobs:
        scheduled_time = job.next_run_time
        scheduled_time += datetime.timedelta(hours=-8)
        reminder = job.args[1]
        await msg.channel.send(f'Reminder: "{reminder}" scheduled for {scheduled_time.strftime("%I:%M %p %m/%d/%Y")}')


# helper method for printing out the reminder when the time is reached
async def remind_user(userid, message, channel):
    await channel.send(f'Reminder for <@' + str(userid) + '>: "' + message + '"')


# runs the bot with the hidden token
with open("token.txt") as file:
    token = file.read()

client.run(token)
