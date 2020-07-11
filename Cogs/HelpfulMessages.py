import discord
from discord.ext import commands


class HelpfulMessages(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name='setup',
        brief="Get the link to the current MultiWorld setup video",
        help="Get the link to the current MultiWorld setup video"
    )
    @commands.is_owner()
    async def get_setup_video(self, ctx: commands.Context):
        await ctx.send("https://www.youtube.com/watch?v=8g87Zhf5p_c")
        pass


# All cogs must have this function
def setup(bot: commands.Bot):
    bot.add_cog(HelpfulMessages(bot))
