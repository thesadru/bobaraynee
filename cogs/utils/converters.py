import re
from operator import attrgetter
from typing import Any

import disnake
from disnake.ext import commands

id_pattern = re.compile(r'[0-9]{15,19}')

def clean_inter_content(
    *,
    fix_channel_mentions: bool = False,
    use_nicknames: bool = True,
    escape_markdown: bool = False,
    remove_markdown: bool = False,
):
    async def convert(inter: disnake.ApplicationCommandInteraction, argument: str):
        if inter.guild:
            def resolve_member(id: int) -> str:
                m = inter.guild.get_member(id)
                return f'@{m.display_name if use_nicknames else m.name}' if m else '@deleted-user'

            def resolve_role(id: int) -> str:
                r = inter.guild.get_role(id)
                return f'@{r.name}' if r else '@deleted-role'
        else:
            def resolve_member(id: int) -> str:
                m = inter.bot.get_user(id)
                return f'@{m.name}' if m else '@deleted-user'

            def resolve_role(id: int) -> str:
                return '@deleted-role'

        if fix_channel_mentions and inter.guild:
            def resolve_channel(id: int) -> str:
                c = inter.guild.get_channel(id)
                return f'#{c.name}' if c else '#deleted-channel'
        else:
            def resolve_channel(id: int) -> str:
                return f'<#{id}>'

        transforms = {
            '@': resolve_member,
            '@!': resolve_member,
            '#': resolve_channel,
            '@&': resolve_role,
        }

        def repl(match: re.Match) -> str:
            type = match[1]
            id = int(match[2])
            transformed = transforms[type](id)
            return transformed

        result = re.sub(r'<(@[!&]?|#)([0-9]{15,20})>', repl, argument)
        if escape_markdown:
            result = disnake.utils.escape_markdown(result)
        elif remove_markdown:
            result = disnake.utils.remove_markdown(result)

        # Completely ensure no mentions escape:
        return disnake.utils.escape_mentions(result)

    return convert

async def tag_name(inter: disnake.ApplicationCommandInteraction, argument: str):
    converted = await clean_inter_content()(inter, argument)
    lower = converted.lower().strip()

    if not lower:
        raise commands.BadArgument('Missing tag name')

    if len(lower) > 50:
        raise commands.BadArgument('Tag name must be less than 50')

    return lower

def user(**attrs):
    async def convert(inter: disnake.ApplicationCommandInteraction, argument: str):
        if not argument.isdigit():
            raise TypeError('This field must be a integer')
        match = re.match(id_pattern, argument)
        if match is None:
            raise ValueError(f'{argument!r} is not an id')
        id = int(match.group())
        user = await inter.bot.fetch_user(id)

        # global -> local
        _all = all
        attrget = attrgetter

        # Special case the single element call
        if len(attrs) == 1:
            k, v = attrs.popitem()
            pred = attrget(k.replace('__', '.'))
            if pred(user) == v:
                return user
            raise disnake.NotFound('User does not match requiroments.')

        converted = [(attrget(attr.replace('__', '.')), value) for attr, value in attrs.items()]

        if _all(pred(user) == value for pred, value in converted):
            return user
        raise disnake.NotFound('User does not match requiroments.')
    return convert
