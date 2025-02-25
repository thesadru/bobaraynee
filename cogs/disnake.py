import io
import re
import os
from typing import cast
import zlib

from disnake.ext import commands
from disnake.utils import oauth_url
import disnake
import aiohttp

from .utils import fuzzy
from .utils.views import Confirm, _BaseView
from .utils.converters import user
from bot import DisnakeHelper

DISNAKE_GUILD_ID = 808030843078836254
DISNAKE_ADDBOT_CHANNEL = 808032994668576829
DISNAKE_MODS = (301295716066787332, 428483942329614336)

DOC_KEYS = {
    'latest': 'https://disnake.readthedocs.io/en/latest/',
    'python': 'https://docs.python.org/3',
    'dislash': 'https://dislashpy.readthedocs.io/en/latest/',
    'dpy-master': 'http://discordpy.readthedocs.io/en/master/'
}
Branches = commands.option_enum(
    {
        'disnake latest': 'latest',
        'python 3.x': 'python',
        'dislash.py': 'dislash',
        'discord.py master': 'dpy-master'
    }
)

class SphinxObjectFileReader:
    # Inspired by Sphinx's InventoryFileReader
    BUFSIZE = 16 * 1024

    def __init__(self, buffer):
        self.stream = io.BytesIO(buffer)

    def readline(self):
        return self.stream.readline().decode('utf-8')

    def skipline(self):
        self.stream.readline()

    def read_compressed_chunks(self):
        decompressor = zlib.decompressobj()
        while True:
            chunk = self.stream.read(self.BUFSIZE)
            if len(chunk) == 0:
                break
            yield decompressor.decompress(chunk)
        yield decompressor.flush()

    def read_compressed_lines(self):
        buf = b''
        for chunk in self.read_compressed_chunks():
            buf += chunk
            pos = buf.find(b'\n')
            while pos != -1:
                yield buf[:pos].decode('utf-8')
                buf = buf[pos + 1:]
                pos = buf.find(b'\n')

class AddBotView(_BaseView):
    def __init__(self, bot_owner: disnake.User, community_bot: disnake.User, *, listen_to, embed):
        super().__init__(listen_to=listen_to, timeout=None)
        self.bot_owner = bot_owner
        self.community_bot = community_bot
        self.embed = embed

    @disnake.ui.button(label='Accept', style=disnake.ButtonStyle.success)
    async def do_accept(self, _, interaction: disnake.MessageInteraction):
        self.embed.colour = disnake.Colour.green()

        await interaction.response.edit_message(
            content=f'{self.bot_owner.mention} will be aware about adding a bot.',
            embed=self.embed,
            view=None
        )
        await self.bot_owner.send(f'Your bot {self.community_bot.mention} was invited to disnake server.')

    @disnake.ui.button(label='Deny', style=disnake.ButtonStyle.danger)
    async def do_accept(self, _, interaction: disnake.MessageInteraction):
        self.embed.colour = disnake.Colour.red()

        await interaction.response.edit_message(
            content=f'{self.bot_owner.mention} will be aware about rejecting a bot.',
            embed=self.embed,
            view=None
        )
        await self.bot_owner.send(f'Bot {self.community_bot.mention} invitation was rejected.')

class Disnake(commands.Cog, name='disnake'):
    """Docs and other disnake's guild things."""

    def __init__(self, bot: DisnakeHelper):
        self.bot = bot

    def parse_object_inv(self, stream: SphinxObjectFileReader, url: str):
        # key: URL
        # n.b.: key doesn't have `discord` or `discord.ext.commands` namespaces
        result = {}

        # first line is version info
        inv_version = stream.readline().rstrip()

        if inv_version != '# Sphinx inventory version 2':
            raise RuntimeError('Invalid objects.inv file version.')

        # next line is "# Project: <name>"
        # then after that is "# Version: <version>"
        projname = stream.readline().rstrip()[11:]
        version = stream.readline().rstrip()[11:]

        # next line says if it's a zlib header
        line = stream.readline()
        if 'zlib' not in line:
            raise RuntimeError('Invalid objects.inv file, not z-lib compatible.')

        # This code mostly comes from the Sphinx repository.
        entry_regex = re.compile(r'(?x)(.+?)\s+(\S*:\S*)\s+(-?\d+)\s+(\S+)\s+(.*)')
        for line in stream.read_compressed_lines():
            match = entry_regex.match(line.rstrip())
            if not match:
                continue

            name, directive, prio, location, dispname = match.groups()
            domain, _, subdirective = directive.partition(':')
            if directive == 'py:module' and name in result:
                # From the Sphinx Repository:
                # due to a bug in 1.1 and below,
                # two inventory entries are created
                # for Python modules, and the first
                # one is correct
                continue

            # Most documentation pages have a label
            if directive == 'std:doc':
                subdirective = 'label'

            if location.endswith('$'):
                location = location[:-1] + name

            key = name if dispname == '-' else dispname
            prefix = f'{subdirective}:' if domain == 'std' else ''

            if projname == 'disnake':
                key = key.replace('disnake.ext.commands.', '').replace('disnake.', '')

            result[f'{prefix}{key}'] = os.path.join(url, location)

        return result

    async def prepare_cache(self):
        cache = {}
        for key, page in DOC_KEYS.items():
            cache[key] = {}
            async with aiohttp.ClientSession(loop=self.bot.loop) as session:
                async with session.get(page + '/objects.inv') as resp:
                    if resp.status != 200:
                        raise RuntimeError('Cannot build rtfm lookup table, try again later.')

                    stream = SphinxObjectFileReader(await resp.read())
                    cache[key] = self.parse_object_inv(stream, page)

        self._cache = cache
    
    async def do_rtfm(self, inter: disnake.ApplicationCommandInteraction, key: str, obj):
        if not hasattr(self, '_cache'):
            await self.prepare_cache()

        if obj is None:
            await inter.response.send_message(DOC_KEYS[key])
            return

        obj = re.sub(r'^(?:disnake\.(?:ext\.)?)?(?:commands\.)?(.+)', r'\1', obj)

        if key.startswith(('latest', 'dpy-master')):
            # point the abc.Messageable types properly:
            q = obj.lower()
            for name in dir(disnake.abc.Messageable):
                if name[0] == '_':
                    continue
                if q == name:
                    obj = f'abc.Messageable.{name}'
                    break

        cache = list(self._cache[key].items())

        matches = fuzzy.finder(obj, cache, key=lambda t: t[0], lazy=False)[:8]

        if len(matches) == 0:
            return await inter.response.send_message('Could not find anything. Sorry.')

        e = disnake.Embed(
            description = '\n'.join(f'[`{key}`]({url})' for key, url in matches),
            colour=0x0084c7
        )
        await inter.response.send_message(embed=e)

        # if ctx.guild and ctx.guild.id in self.bot._test_guilds:
        #     query = 'INSERT INTO rtfm (user_id) VALUES ($1) ON CONFLICT (user_id) DO UPDATE SET count = rtfm.count + 1;'
        #     await ctx.db.execute(query, ctx.author.id)

    @commands.slash_command()
    async def rtfm(
        self,
        inter,
        object: str,
        language: Branches = commands.param('latest')
    ):
        """
        Gives you a documentation link for a selected doc entity.
        Parameters
        ----------
        object: Requested object
        docs: Documentation key
        """
        await self.do_rtfm(inter, language, object)
    
    @commands.slash_command()
    async def addbot(
        self,
        inter: disnake.ApplicationCommandInteraction,
        bot_id: str = commands.param(conv=user(bot=True)),
        reason: str = commands.param()
    ):
        """
        Requests your bot to be added to the server.
        Parameters
        ----------
        bot_id: Bot user id
        reason: Why you want your bot here?
        """
        bot: disnake.User = bot_id

        async def callback(res, inter: disnake.MessageInteraction):
            if res is None:
                content = 'You took too long.'
            elif res:
                content = 'You will get a DM regarding the status of your bot, so make sure you have them on.'
            else:
                content = 'Canceled'
            await inter.response.edit_message(view=None)
            await inter.followup.send(content)

        view = Confirm(callback, listen_to=(inter.author.id,))
        await inter.response.send_message(
            f'You\'re going to add {bot.mention} on this server.\n'
            'To agree, please press "Confirm" button',
            view = view
        )
        await view.wait()
        if not view.value:
            return

        url = oauth_url(bot.id, guild=disnake.Object(DISNAKE_GUILD_ID), scopes=('bot', 'application.commands'))
        e = disnake.Embed(description=reason, color=disnake.Colour.orange())
        e.set_author(name=inter.author.display_name, icon_url=inter.author.display_avatar)
        e.set_thumbnail(url=bot.avatar)
        e.add_field(name='Name', value=str(bot))
        e.add_field(name='Link', value=f'[Invite URL]({url})')
        e.add_field(name='ID', value=bot.id, inline=False)

        view = AddBotView(inter.author._user, bot, listen_to=DISNAKE_MODS, embed=e)

        msg = await self.bot.get_partial_messageable(DISNAKE_ADDBOT_CHANNEL).send(embed=e, view=view)


def setup(bot):
    bot.add_cog(Disnake(bot))
