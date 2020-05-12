import aiohttp
import aiofiles
import functools
import json
import os
import requests
import socket
import string
import sys
import websockets
import zlib
from asyncio import sleep
from dotenv import load_dotenv
from discord.ext import commands
from random import choice, randrange
from re import findall

# Skip Berserker's automatically attempting to install requirements from a file
sys.path.append("MultiWorldUtilities")
import ModuleUpdate

ModuleUpdate.update_ran = True

# Import Berserker's MultiServer file
import MultiServer

# Find the public ip address of the current machine, and possibly a domain name
load_dotenv()
MULTIWORLD_HOST_IP = os.getenv('PUBLIC_IP', requests.get('https://checkip.amazonaws.com').text.strip())
MULTIWORLD_DOMAIN = os.getenv('HOST_URL', MULTIWORLD_HOST_IP)


class MultiworldHost(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    async def gen_token(prefix: str = '') -> str:
        return prefix.join(choice(string.ascii_uppercase) for x in range(4))

    @staticmethod
    def get_open_port():
        # Choose a port from 5000 to 7000 and ensure it is not in use
        while True:
            port = randrange(5000, 7000)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if not sock.connect_ex(('localhost', port)) == 0:
                    return port

    @staticmethod
    def parse_multidata(filepath):
        with open(filepath, 'rb') as f:
            return json.loads(zlib.decompress(f.read()).decode("utf-8"))

    @staticmethod
    def create_multi_server(port: int, token: str):
        # Create and configure MultiWorld server
        multidata = MultiworldHost.parse_multidata(f'multidata/{token}_multidata')
        server_opts = multidata['server_options']

        multi = MultiServer.Context('0.0.0.0', port, None, int(server_opts['location_check_points']),
                                    int(server_opts['hint_cost']), not server_opts['disable_item_cheat'],
                                    not server_opts['disable_client_forfeit'])
        multi.data_filename = f'multidata/{token}_multidata'
        multi.save_filename = f'multidata/{token}_multisave'

        # Configure multiserver
        for team, names in enumerate(multidata['names']):
            for player, name in enumerate(names, 1):
                multi.player_names[(team, player)] = name
        multi.rom_names = {tuple(rom): (team, slot) for slot, team, rom in multidata['roms']}
        multi.remote_items = set(multidata['remote_items'])
        multi.locations = {tuple(k): tuple(v) for k, v in multidata['locations']}

        # Configure multisave
        if os.path.exists(multi.save_filename):
            with open(multi.save_filename, 'rb') as f:
                json_obj = json.loads(zlib.decompress(f.read()).decode("utf-8"))
                multi.set_save(json_obj)

        multi.server = websockets.serve(functools.partial(MultiServer.server, ctx=multi), multi.host, multi.port,
                                        ping_timeout=None, ping_interval=None)
        return multi

    @commands.command(
        name='host-game',
        brief="Use AginahBot to host your multiworld",
        help='Upload a .multidata file to have AginahBot host a multiworld game. The game will be automatically '
             'closed after eight hours. Server options are loaded from the multidata file.\n\n'
             'Usage: !aginah host-game',
    )
    async def host_game(self, ctx: commands.Context):
        if not ctx.message.attachments:
            await ctx.send("Did you forget to attach a multidata file?")
            return

        # Generate a multiworld token and ensure it is not in use already
        while True:
            token = await self.gen_token()
            if token not in ctx.bot.servers:
                break

        # Save the multidata file to the /multidata folder
        multidata_url = ctx.message.attachments[0].url
        async with aiohttp.ClientSession() as session:
            async with session.get(multidata_url) as res:
                async with aiofiles.open(f'multidata/{token}_multidata', 'wb') as multidata_file:
                    await multidata_file.write(await res.read())

        # Find an open port
        port = self.get_open_port()

        # Host game and store in ctx.bot.servers
        try:
            ctx.bot.servers[token] = {
                'host': MULTIWORLD_HOST_IP,
                'port': port,
                'game': self.create_multi_server(port, token)
            }
            await ctx.bot.servers[token]['game'].server
        except zlib.error:
            # Do not retain invalid multidata file
            if os.path.exists(f'multidata/{token}_multidata'):
                os.remove(f'multidata/{token}_multidata')

            # Do not retain invalid multisave file
            if os.path.exists(f'multidata/{token}_multisave'):
                os.remove(f'multidata/{token}_multisave')

            await ctx.send("Your multidata file appears to be invalid.")
            return

        # Send host details to client
        await ctx.send(f"Your game has been hosted.\nHost: `{MULTIWORLD_DOMAIN}:{port}`\nToken: `{token}`")

        # Kill the server after eight hours
        await sleep(8 * 60 * 60)
        if token in ctx.bot.servers:
            await ctx.bot.servers[token]['game'].server.ws_server._close()
            print(f"Automatically closed game with token {token} after eight hours.")

    @commands.command(
        name='resume-game',
        brief='Re-host a game previously hosted by AginahBot',
        help='Re-host a timed-out or closed game previously hosted by AginahBot. The game will automatically close '
             'after eight hours Server options are loaded from the multidata file.\n\n'
             'Usage: !aginah resume-game {token}'
    )
    async def resume_game(self, ctx: commands.Context):
        # Parse command arguments from ctx
        cmd_args = ctx.message.content.split()
        token = cmd_args[2] if 0 <= 2 < len(cmd_args) else None
        check_points = cmd_args[3] if 0 <= 3 < len(cmd_args) else 1
        hint_cost = cmd_args[4] if 0 <= 4 < len(cmd_args) else 50
        allow_cheats = True if 0 <= 5 < len(cmd_args) else False
        allow_forfeit = False if 0 <= 6 < len(cmd_args) else False

        # Ensure a token is provided
        if not token:
            await ctx.send('You forgot to give me a token! Use `!aginah help resume-game` for more details.')
            return

        # Ensure token is of correct length
        match = findall("^[A-z]{4}$", token)
        if not len(match) == 1:
            await ctx.send("That token doesn't look right. Use `!aginah help resume-game` for more details.")
            return

        # Enforce token formatting
        token = str(token).upper()

        # Check if game is already running
        if token in ctx.bot.servers:
            await ctx.send(f'It looks like a game with that token is already underway!\n'
                           f'Host: {MULTIWORLD_DOMAIN}:{ctx.bot.servers[token]["port"]}')
            return

        # Check for presence of multidata file with given token
        if not os.path.exists(f'multidata/{token}_multidata'):
            await ctx.send('Sorry, no previous game with that token could be found.')
            return

        # Find an open port
        port = self.get_open_port()

        # Host game and store in ctx.bot.servers
        ctx.bot.servers[token] = {
            'host': MULTIWORLD_HOST_IP,
            'port': port,
            'game': self.create_multi_server(port, token)
        }
        await ctx.bot.servers[token]['game'].server

        # Send host details to client
        await ctx.send(f"Your game has been hosted.\nHost: `{MULTIWORLD_DOMAIN}:{port}`")

        # Kill the server after eight hours
        await sleep(8 * 60 * 60)
        if token in ctx.bot.servers:
            await ctx.bot.servers[token]['game'].server.ws_server._close()
            print(f"Automatically closed game with token {token} after eight hours.")

    @commands.command(
        name='end-game',
        brief='Close a multiworld server',
        help='Shut down a multiworld server. Current players will be disconnected, new players will '
             'be unable to join, and the game will not be able to be resumed.\n\n'
             'Usage: !aginah end-game {token}',
    )
    @commands.is_owner()
    async def end_game(self, ctx: commands.Context):
        # Parse command
        matches = findall("^\!aginah end-game ([A-z]{4})$", ctx.message.content)
        if not len(matches) == 1:
            await ctx.send("Your command doesn't look right. Use `!aginah help end-game` for more info.")
            return

        # Enforce token formatting
        token = str(matches[0]).upper()

        if token not in ctx.bot.servers:
            await ctx.send("No game with that token is currently running")
            return

        # Kill the server if it exists
        if token in ctx.bot.servers:
            await ctx.bot.servers[token]['game'].server.ws_server._close()
            del ctx.bot.servers[token]

        # Delete multidata file
        if os.path.exists(f'multidata/{token}_multidata'):
            os.remove(f'multidata/{token}_multidata')

        # Delete multisave file
        if os.path.exists(f'multidata/{token}_multisave'):
            os.remove(f'multidata/{token}_multisave')

        await ctx.send("The game has been closed.")

    @commands.command(
        name='purge-files',
        brief='Delete all multidata and multisave files not currently in use',
        help='Delete all multidata and multisave files in the ./multidata directory which are not currently '
             'in use by an active server\n\n'
             'Usage: !aginah purge-files'
    )
    @commands.is_owner()
    async def purge_files(self, ctx: commands.Context):
        # Loop over all files in the ./multidata directory
        for file in os.scandir('./multidata'):
            # Determine file token string
            token = findall("^([A-Z]{4})_(multisave|multidata)$", file.name)
            # If a token match is found and a game with that token is not currently running, delete the file
            if token and token[0] not in ctx.bot.servers:
                os.remove(file.path)


def setup(bot: commands.Bot):
    bot.add_cog(MultiworldHost(bot))