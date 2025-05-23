import datetime
import discord

from discord import AutoModRuleTriggerType, AutoModRuleActionType, Interaction, ButtonStyle, Embed, AutoModRule, \
    AutoModRuleAction, TextStyle
from discord.ui import Select, Button, TextInput, Modal, View
from discord.utils import format_dt

from typing import TYPE_CHECKING

from utils import WarnType, WarnState
from utils.checks import check_staff
from utils.database import ValidWarnEntry, DeletedWarnEntry
from utils.utils import parse_time, gen_color, text_to_discord_file, create_error_embed, create_userinfo_embed
from utils.warns import WARN_EXPIRATION

if TYPE_CHECKING:
    from kurisu import Kurisu, Optional


class BasePaginator:
    """Serves as base class for paginators for a BasePaginatedView, not to be used as-is"""
    def __init__(self, n_pages: int):
        self.n_pages = n_pages
        self.idx = 0
        self.pages: dict[int, Embed] = {}

    def previous(self):
        self.idx = max(self.idx - 1, 0)

    def next(self):
        self.idx = min(self.idx + 1, self.n_pages - 1)

    def first(self):
        self.idx = 0

    def last(self):
        self.idx = self.n_pages - 1

    def is_first(self):
        return self.idx == 0

    def is_last(self):
        return self.idx == self.n_pages - 1

    def current(self):
        raise NotImplementedError


class BaseView(View):
    """Base class for all other views"""

    def __init__(self, author: 'Optional[discord.Member | discord.User]' = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.message: 'Optional[discord.Message | discord.InteractionMessage]' = None
        self.author = author

    async def on_timeout(self) -> None:
        if self.message:
            await self.message.edit(view=None)
        self.stop()

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.author and interaction.user.id != self.author.id:
            await interaction.response.send_message("This view is not for you.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: Interaction, error: Exception, item):
        embed = create_error_embed(interaction, error)
        await interaction.client.channels['bot-err'].send(embed=embed)


class BasePaginatedView(BaseView):
    """Base class for a paginated view using a BasePaginator subclass"""

    def __init__(self, paginator: BasePaginator, author: 'Optional[discord.Member | discord.User]' = None,
                 timeout: int = 30):
        super().__init__(timeout=timeout, author=author)
        self.paginator = paginator

        if self.paginator.n_pages == 1:
            self.disable_buttons()

    def reset_buttons(self):
        self.first_page.disabled = True
        self.prev_page.disabled = True
        self.next_page.disabled = False
        self.last_page.disabled = False

    def disable_buttons(self):
        self.first_page.disabled = True
        self.prev_page.disabled = True
        self.next_page.disabled = True
        self.last_page.disabled = True

    @discord.ui.button(label="<<", style=ButtonStyle.secondary, disabled=True)
    async def first_page(self, interaction: Interaction, button: Button):
        self.first_page.disabled = True
        self.prev_page.disabled = True
        self.next_page.disabled = False
        self.last_page.disabled = False
        self.paginator.first()
        await interaction.response.edit_message(embed=self.paginator.current(), view=self)

    @discord.ui.button(label='Back', style=ButtonStyle.primary, disabled=True)
    async def prev_page(self, interaction: Interaction, button: Button):
        self.next_page.disabled = False
        self.last_page.disabled = False
        self.paginator.previous()
        if self.paginator.is_first():
            self.first_page.disabled = True
            self.prev_page.disabled = True
        await interaction.response.edit_message(embed=self.paginator.current(), view=self)

    @discord.ui.button(label='Next', style=ButtonStyle.primary)
    async def next_page(self, interaction: Interaction, button: Button):
        self.first_page.disabled = False
        self.prev_page.disabled = False
        self.paginator.next()
        if self.paginator.is_last():
            self.next_page.disabled = True
            self.last_page.disabled = True
        await interaction.response.edit_message(embed=self.paginator.current(), view=self)

    @discord.ui.button(label=">>", style=ButtonStyle.secondary)
    async def last_page(self, interaction: Interaction, button: Button):
        self.first_page.disabled = False
        self.prev_page.disabled = False
        self.next_page.disabled = True
        self.last_page.disabled = True
        self.paginator.last()
        await interaction.response.edit_message(embed=self.paginator.current(), view=self)

    @discord.ui.button(label="Exit", style=ButtonStyle.red)
    async def remove(self, interaction: Interaction, button: Button):
        await interaction.response.edit_message(view=None)
        self.stop()


class PaginatedEmbedView(BasePaginatedView):
    def __init__(self, paginator: BasePaginator, timeout: int = 20,
                 author: 'Optional[discord.Member | discord.User]' = None):
        super().__init__(paginator=paginator, timeout=timeout, author=author)
        if self.paginator.n_pages == 1:
            self.clear_items()


class EmbedListPaginator(BasePaginator):
    def __init__(self, embeds: list[Embed]):
        super().__init__(n_pages=len(embeds))
        self.pages = {n: embed for n, embed in enumerate(embeds)}

    def current(self):
        return self.pages[self.idx]


class VoteButton(Button['SimpleVoteView']):
    label: str

    def __init__(self, custom_id: str, label: str, style: ButtonStyle = ButtonStyle.secondary):
        super().__init__(style=style, label=label, custom_id=custom_id)

    async def callback(self, interaction: Interaction):
        assert self.view is not None
        if self.view.staff_only and not check_staff(interaction.client, 'Helper', interaction.user.id):
            await interaction.response.send_message("You aren't allowed to vote.", ephemeral=True)
            return
        await interaction.client.extras.add_vote(self.view.custom_id, interaction.user.id, self.label)
        await interaction.response.send_message("Vote added.", ephemeral=True)


class VoteButtonEnd(Button['SimpleVoteView']):
    def __init__(self, custom_id: str, style: ButtonStyle = ButtonStyle.red):
        super().__init__(style=style, label='End', custom_id=custom_id)

    async def callback(self, interaction: Interaction):
        assert self.view is not None
        if interaction.user.id == self.view.author_id:
            # Try to remove the view
            await interaction.response.edit_message(view=None)

            await self.view.calculate_votes()
            results = "results:\n" + '\n'.join(f"{op}: {count}" for op, count in self.view.count.items())
            await interaction.followup.send(
                f"Vote started {format_dt(self.view.start, style='R')} has finished.\n{results}")
            self.view.stop()
            await interaction.client.extras.delete_voteview(self.view.custom_id)
        else:
            await interaction.response.send_message("Only the vote creator can end it", ephemeral=True)


class SimpleVoteView(BaseView):
    def __init__(self, bot: 'Kurisu', author_id: int, options: list[str], custom_id: int, start: datetime.datetime,
                 staff_only: bool = False):
        super().__init__(timeout=None)
        self.extras = bot.extras
        self.author_id = author_id
        self.custom_id = custom_id
        self.start = start
        self.staff_only = staff_only
        self.count: dict[str, int] = {}
        for n, option in enumerate(options):
            self.count[option] = 0
            self.add_item(VoteButton(label=option, custom_id=f"{custom_id}_{n}"))
        self.add_item(VoteButtonEnd(custom_id=f"{custom_id}_{len(self.children) + 1}"))

    async def calculate_votes(self):
        async for vote in self.extras.get_votes(self.custom_id):
            self.count[vote.option] = self.count[vote.option] + 1


class ConfirmationButtons(BaseView):
    def __init__(self, author: discord.Member | discord.User):
        super().__init__(timeout=30, author=author)
        self.value = None

    @discord.ui.button(label="Yes", style=ButtonStyle.green)
    async def confirm_button(
            self, interaction: Interaction, button: Button
    ):
        self.value = True
        # Just so the interaction doesn't fail
        await interaction.response.send_message("Confirmed", ephemeral=True)
        self.stop()

    @discord.ui.button(label="No", style=ButtonStyle.red)
    async def deny_button(
            self, interaction: Interaction, button: Button
    ):
        self.value = False
        # Just so the interaction doesn't fail
        await interaction.response.send_message("Denied", ephemeral=True)
        self.stop()


class TimeoutInput(Modal):
    duration = TextInput(label='Timeout duration (0s for disabling)', style=TextStyle.short,
                         required=True, placeholder="Time in #d#h#m#s format.")

    def __init__(self, automod_rule: AutoModRule):
        super().__init__(title='Timeout Update')
        self.automod_rule = automod_rule

    async def on_submit(self, interaction: Interaction):
        secs = parse_time(self.duration.value)
        if secs == -1:
            await interaction.response.send_message('Invalid time format', ephemeral=True)
        elif secs > 2419200:
            await interaction.response.send_message('Timeouts can\'t be longer than 28 days.', ephemeral=True)
        elif secs > 0:
            action: Optional[AutoModRuleAction] = discord.utils.get(self.automod_rule.actions,
                                                                    type=AutoModRuleActionType.timeout)
            delta = datetime.timedelta(seconds=secs)
            if action:
                action.duration = delta
                await self.automod_rule.edit(actions=self.automod_rule.actions)
            else:
                new_action = AutoModRuleAction(duration=delta)
                self.automod_rule.actions.append(new_action)
                await self.automod_rule.edit(actions=self.automod_rule.actions)
            await interaction.response.send_message('Timeout updated succesfully. Refresh the view.', ephemeral=True)
        elif secs == 0:
            action: Optional[AutoModRuleAction] = discord.utils.get(self.automod_rule.actions,
                                                                    type=AutoModRuleActionType.timeout)
            if action:
                self.automod_rule.actions.remove(action)
                await self.automod_rule.edit(actions=self.automod_rule.actions)
            await interaction.response.send_message('Timeout removed succesfully. Refresh the view.', ephemeral=True)


class AutoModRulesView(BaseView):
    def __init__(self, automod_rules: list[AutoModRule], author: discord.Member):
        super().__init__(author=author)
        self.selected: Optional[AutoModRule] = None
        self.add_item(AutoModRuleSelect(automod_rules))
        self.embed_colour = gen_color(author.id)
        self.default_embed = Embed(title="AutoMod Rules", description="Select an AutoMod Rule",
                                   colour=self.embed_colour)

    @discord.ui.button(label="General", style=ButtonStyle.secondary, disabled=True)
    async def general_information(self, interaction: Interaction, button: Button):
        embed = self.create_rule_embed()
        self.set_timeout.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Actions", style=ButtonStyle.secondary, disabled=True)
    async def actions_information(self, interaction: Interaction, button: Button):
        assert self.selected is not None
        embed = self.create_actions_embed()
        if self.selected.trigger.type not in (AutoModRuleTriggerType.keyword_preset, AutoModRuleTriggerType.spam):
            self.set_timeout.disabled = False
        self.general_information.disabled = False
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Set timeout", style=ButtonStyle.secondary, disabled=True)
    async def set_timeout(self, interaction: Interaction, button: Button):
        assert self.selected is not None
        await interaction.response.send_modal(TimeoutInput(self.selected))

    @discord.ui.button(label="Export keywords", style=ButtonStyle.secondary, disabled=True)
    async def export_keywords(self, interaction: Interaction, button: Button):
        assert self.selected is not None
        text = '\n'.join(self.selected.trigger.keyword_filter)
        file = text_to_discord_file(text, name='export.txt')
        await interaction.response.send_message(file=file, ephemeral=True)

    @discord.ui.button(label="Stop", style=ButtonStyle.red, disabled=False, row=0)
    async def stop_button(self, interaction: Interaction, button: Button):
        await interaction.response.edit_message(view=None)
        self.stop()

    def create_rule_embed(self) -> Embed:
        assert self.selected is not None
        embed = Embed(title=f"{self.selected.name} Rule", colour=self.embed_colour)
        embed.add_field(name="Enabled", value=self.selected.enabled)
        embed.add_field(name="Creator", value=self.selected.creator)
        trigger_type = self.selected.trigger.type
        embed.add_field(name="Trigger", value=trigger_type.name)
        if trigger_type is AutoModRuleTriggerType.keyword:
            embed.add_field(name="Keywords", value=len(self.selected.trigger.keyword_filter))
            embed.add_field(name="Regex patts", value=len(self.selected.trigger.regex_patterns))
        elif trigger_type is AutoModRuleTriggerType.mention_spam:
            embed.add_field(name="Mention limit", value=self.selected.trigger.mention_limit)
        elif trigger_type is AutoModRuleTriggerType.keyword_preset:
            presets = ['profanity', 'sexual_content', 'slurs']
            embed.add_field(name="Presets", value=' '.join(
                preset for preset in presets if getattr(self.selected.trigger.presets, preset)))
        return embed

    def create_actions_embed(self):
        assert self.selected is not None
        embed = Embed(title=f"{self.selected.name} Trigger", colour=self.embed_colour)
        embed.add_field(name="Actions", value=' '.join(f'`{action.type.name}`' for action in self.selected.actions))
        if alert_action := discord.utils.get(self.selected.actions, type=AutoModRuleActionType.send_alert_message):
            embed.add_field(name="Alert Channel", value=f"<#{alert_action.channel_id}>")
        if timeout_action := discord.utils.get(self.selected.actions, type=AutoModRuleActionType.timeout):
            embed.add_field(name="Timeout Duration", value=str(timeout_action.duration))
        return embed


class AutoModRuleSelect(Select['AutoModRulesView']):

    def __init__(self, automod_rules: list[AutoModRule]):
        super().__init__(placeholder="Select an AutoMod rule.")
        self.rules: dict = {r.name: r for r in automod_rules}
        self.populate()

    def populate(self):
        for rule in self.rules.values():
            self.add_option(label=rule.name, value=rule.name, description="Enabled" if rule.enabled else "Disabled")

    async def callback(self, interaction: Interaction):
        assert self.view is not None
        self.view.selected = self.rules[self.values[0]]
        self.view.set_timeout.disabled = True
        if self.view.selected:
            self.view.actions_information.disabled = False
            self.view.export_keywords.disabled = self.view.selected.trigger.type is not AutoModRuleTriggerType.keyword
            embed = self.view.create_rule_embed()
        else:
            embed = self.view.default_embed
        await interaction.response.edit_message(view=self.view, embed=embed)


class WarnManagerView(BaseView):

    author: discord.Member
    embed: discord.Embed
    warn: 'Optional[DeletedWarnEntry|ValidWarnEntry]'

    def __init__(self, bot: 'Kurisu', author: discord.Member,
                 user: discord.Member | discord.User):
        super().__init__(timeout=20, author=author)
        self.user = user
        self.bot = bot
        self.embed_colour = gen_color(author.id)
        self.warn = None

    async def init(self):
        await self.load_warns()
        self.embed = await create_userinfo_embed(self.user, self.author.guild)
        if self.warns:
            self.add_item(WarnSelect(self.warns, placeholder="Select a Warn"))
        if self.deleted_warns:
            self.add_item(WarnSelect(self.deleted_warns, placeholder="Select a deleted/expired Warn"))

    async def reload(self):
        self.clear_items()
        await self.init()

    async def load_warns(self):
        self.warns = [w async for w in self.bot.warns.get_warnings(self.user)]
        self.deleted_warns = [w async for w in self.bot.warns.get_deleted_warnings(self.user)]

    async def create_warn_embed(self, warn: ValidWarnEntry | DeletedWarnEntry):
        embed = discord.Embed(color=discord.Color.dark_red())
        issuer = self.bot.get_user(warn.issuer_id)
        embed.add_field(name="Type", value=WarnType(warn.type).name)
        embed.add_field(name="State", value=WarnState(warn.state).name)
        embed.add_field(name="User", value=self.user.mention)
        embed.add_field(name="Issuer", value=issuer.mention if issuer else warn.issuer_id)
        embed.add_field(name="Date", value=format_dt(warn.date))
        embed.add_field(name="Reason", value=warn.reason)
        value = ""
        match warn.state:
            case WarnState.Valid:
                number = await self.bot.warns.get_warning_number(warn.warn_id)
                embed.title = f"Warn {number}"
                if warn.type == WarnType.Ephemeral:
                    value += f"\nExpires on {format_dt(warn.date + WARN_EXPIRATION)}"
                else:
                    value += "\nThis warn doesn't expire"
            case WarnState.Expired:
                embed.title = "Expired Warn"
                embed.add_field(name="Expired on", value=format_dt(warn.deletion_time))
            case WarnState.Deleted:
                embed.title = "Deleted Warn"
                deleter = deleter.mention if (deleter := self.bot.get_user(warn.deleter)) is not None else warn.deleter
                embed.add_field(name="Deleted on", value=format_dt(warn.deletion_time))
                embed.add_field(name="Deletion reason", value=warn.deletion_reason)
                embed.add_field(name="Deleter", value=deleter)
        embed.description = value
        return embed

    async def set_warn(self, interaction: Interaction, warn_id: int):
        warn = await self.bot.warns.get_warning(warn_id)

        if warn is None:
            return

        self.warn = warn
        self.embed = await self.create_warn_embed(warn)
        view = WarnView(self)
        await interaction.response.edit_message(view=view, embed=self.embed)
        await view.wait()

    @discord.ui.button(label="Stop", style=ButtonStyle.red, disabled=False, row=0)
    async def stop_button(self, interaction: Interaction, button: Button):
        await interaction.response.edit_message(view=None)
        self.stop()


class WarnSelect(Select['WarnManagerView']):
    def __init__(self, warns: list[ValidWarnEntry] | list[DeletedWarnEntry], placeholder: str):
        super().__init__(placeholder=placeholder)
        self.populate(warns)

    def populate(self, warns: list[ValidWarnEntry] | list[DeletedWarnEntry]):
        deleted = warns[0].state != 0
        for idx, warn in enumerate(warns):
            if deleted:
                if warn.state == WarnState.Deleted:
                    label = "Deleted Warn"
                else:
                    label = "Expired Warn"
            else:
                label = f"Warn {idx + 1}"
            self.add_option(label=f"{label} {warn.date:%Y-%m-%d %H:%M:%S}", value=str(warn.warn_id), description=warn.reason[:100])

    async def callback(self, interaction: Interaction):
        assert self.view is not None
        await self.view.set_warn(interaction, int(self.values[0]))


class WarnView(BaseView):
    author: discord.Member
    embed: discord.Embed

    def __init__(self, parent: WarnManagerView):
        super().__init__(timeout=20, author=parent.author)
        self.parent = parent
        self.update_labels()

    async def update(self):
        self.parent.warn = await self.parent.bot.warns.get_warning(self.parent.warn.warn_id)
        self.parent.embed = await self.parent.create_warn_embed(self.parent.warn)
        self.update_labels()

    def update_labels(self):
        self.delete_warn.label = "Delete Warn" if self.parent.warn.state == WarnState.Valid else "Restore Warn"
        self.pin_warn.label = "Pin Warn" if self.parent.warn.type == WarnType.Ephemeral else "Unpin Warn"
        self.pin_warn.disabled = self.parent.warn.state != WarnState.Valid

    async def update_message(self, interaction: Interaction):
        await self.update()
        await interaction.response.edit_message(embed=self.parent.embed, view=self)

    async def delete_warning(self, reason):
        await self.parent.bot.warns.delete_warning(self.parent.warn.warn_id, self.author.id, reason)

    @discord.ui.button(label="Delete Warn", style=ButtonStyle.secondary)
    async def delete_warn(self, interaction: Interaction, button: Button):
        if self.parent.warn.state == WarnState.Valid:
            await interaction.response.send_modal(WarnReason(self))
        else:
            await self.parent.bot.warns.restore_warning(self.parent.warn.warn_id)
            await self.update_message(interaction)

    @discord.ui.button(label="Pin Warn", style=ButtonStyle.secondary)
    async def pin_warn(self, interaction: Interaction, button: Button):
        if self.parent.warn.type == WarnType.Pinned:
            await self.parent.bot.warns.unpin_warning(self.parent.warn.warn_id)
        else:
            await self.parent.bot.warns.pin_warning(self.parent.warn.warn_id)
        await self.update_message(interaction)

    @discord.ui.button(label="Back", style=ButtonStyle.secondary)
    async def back(self, interaction: Interaction, button: Button):
        self.stop()
        await self.parent.reload()
        await interaction.response.edit_message(embed=self.parent.embed, view=self.parent)


class WarnReason(Modal):
    title = "Warn Reason"
    reason = TextInput(label='Reason', style=TextStyle.short,
                       required=True, placeholder="Enter reason for warn deletion")

    def __init__(self, parent: WarnView):
        super().__init__()
        self.parent = parent

    async def on_submit(self, interaction: Interaction):
        reason = self.reason.value
        if not reason:
            return
        await self.parent.delete_warning(reason)
        await self.parent.update_message(interaction)
