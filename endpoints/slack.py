import json
import re
import traceback
from typing import Mapping, List, Tuple
from werkzeug import Request, Response
from dify_plugin import Endpoint
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


# ref: https://github.com/fla9ua/markdown_to_mrkdwn
class SlackMarkdownConverter:
    """
    A converter class to transform Markdown text into Slack's mrkdwn format.

    Attributes:
        encoding (str): The character encoding used for the conversion.
        patterns (List[Tuple[str, str]]): A list of regex patterns and their replacements.
    """

    def __init__(self, encoding="utf-8"):
        """
        Initializes the SlackMarkdownConverter with a specified encoding.

        Args:
            encoding (str): The character encoding to use for the conversion. Default is 'utf-8'.
        """
        self.encoding = encoding
        self.in_code_block = False
        self.table_replacements = {}
        # Use compiled regex patterns for better performance
        self.patterns: List[Tuple[re.Pattern, str]] = [
            (re.compile(r"^(\s*)- \[([ ])\] (.+)", re.MULTILINE), r"\1• ☐ \3"),  # Unchecked task list
            (re.compile(r"^(\s*)- \[([xX])\] (.+)", re.MULTILINE), r"\1• ☑ \3"),  # Checked task list
            (re.compile(r"^(\s*)- (.+)", re.MULTILINE), r"\1• \2"),  # Unordered list
            (re.compile(r"^(\s*)(\d+)\. (.+)", re.MULTILINE), r"\1\2. \3"),  # Ordered list
            (re.compile(r"!\[.*?\]\((.+?)\)", re.MULTILINE), r"<\1>"),  # Images to URL
            (re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", re.MULTILINE), r"_\1_"),  # Italic
            (re.compile(r"^###### (.+)$", re.MULTILINE), r"*\1*"), # H6 as bold
            (re.compile(r"^##### (.+)$", re.MULTILINE), r"*\1*"), # H5 as bold
            (re.compile(r"^#### (.+)$", re.MULTILINE), r"*\1*"), # H4 as bold
            (re.compile(r"^### (.+)$", re.MULTILINE), r"*\1*"),  # H3 as bold
            (re.compile(r"^## (.+)$", re.MULTILINE), r"*\1*"),  # H2 as bold
            (re.compile(r"^# (.+)$", re.MULTILINE), r"*\1*"),  # H1 as bold
            (re.compile(r"(^|\s)~\*\*(.+?)\*\*(\s|$)", re.MULTILINE), r"\1 *\2* \3"),  # Bold with space handling
            (re.compile(r"(?<!\*)\*\*(.+?)\*\*(?!\*)", re.MULTILINE), r"*\1*"),  # Bold
            (re.compile(r"__(.+?)__", re.MULTILINE), r"*\1*"),  # Underline as bold
            (re.compile(r"\[(.+?)\]\((.+?)\)", re.MULTILINE), r"<\2|\1>"),  # Links
            (re.compile(r"`(.+?)`", re.MULTILINE), r"`\1`"),  # Inline code
            (re.compile(r"^> (.+)", re.MULTILINE), r"> \1"),  # Blockquote
            (re.compile(r"^(---|\*\*\*|___)$", re.MULTILINE), r"──────────"),  # Horizontal line
            (re.compile(r"~~(.+?)~~", re.MULTILINE), r"~\1~"),  # Strikethrough
        ]
        # Placeholders for triple emphasis
        self.triple_start = "%%BOLDITALIC_START%%"
        self.triple_end = "%%BOLDITALIC_END%%"

    def convert(self, markdown: str) -> str:
        """
        Convert Markdown text to Slack's mrkdwn format.

        Args:
            markdown (str): The Markdown text to convert.

        Returns:
            str: The converted text in Slack's mrkdwn format.
        """
        if not markdown:
            return ""

        try:
            markdown = markdown.strip()

            self.table_replacements = {}

            markdown = self._convert_tables(markdown)

            lines = markdown.split("\n")
            converted_lines = [self._convert_line(line) for line in lines]
            result = "\n".join(converted_lines)

            for placeholder, table in self.table_replacements.items():
                result = result.replace(placeholder, table)

            return result.encode(self.encoding).decode(self.encoding)
        except Exception as e:
            # Log the error for debugging
            return markdown

    def _convert_tables(self, markdown: str) -> str:
        """
        Convert Markdown tables to Slack's mrkdwn format.

        Args:
            markdown (str): The Markdown text containing tables.

        Returns:
            str: The text with tables converted to Slack's format.
        """
        table_pattern = re.compile(
            r"^\|(.+)\|\s*$\n^\|[-:| ]+\|\s*$(\n^\|.+\|\s*$)*", re.MULTILINE
        )

        def convert_table(match):
            original_table = match.group(0)

            table_lines = original_table.strip().split("\n")
            header_line = table_lines[0]
            separator_line = table_lines[1]
            data_lines = table_lines[2:] if len(table_lines) > 2 else []

            headers = [cell.strip() for cell in header_line.strip("|").split("|")]

            rows = []
            for line in data_lines:
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                rows.append(cells)

            result = []
            result.append(" | ".join(f"*{header}*" for header in headers))

            for row in rows:
                result.append(" | ".join(row))

            placeholder = f"%%TABLE_PLACEHOLDER_{hash(original_table)}%%"
            self.table_replacements[placeholder] = "\n".join(result)
            return placeholder

        return table_pattern.sub(convert_table, markdown)

    def _convert_line(self, line: str) -> str:
        """
        Convert a single line of Markdown.

        Args:
            line (str): A single line of Markdown text.

        Returns:
            str: The converted line in Slack's mrkdwn format.
        """
        if line.startswith("%%TABLE_PLACEHOLDER_") and line.endswith("%%"):
            return line

        code_block_match = re.match(r"^```(\w*)$", line)
        if code_block_match:
            language = code_block_match.group(1)
            self.in_code_block = not self.in_code_block
            if self.in_code_block and language:
                return f"```{language}"
            return "```"

        if self.in_code_block:
            return line

        line = re.sub(
            r"(?<!\*)\*\*\*([^*\n]+?)\*\*\*(?!\*)",
            lambda m: f"{self.triple_start}{m.group(1)}{self.triple_end}",
            line,
        )

        for pattern, replacement in self.patterns:
            line = pattern.sub(replacement, line)

        line = re.sub(
            re.escape(self.triple_start) + r"(.*?)" + re.escape(self.triple_end),
            r"*_\1_*",
            line,
            flags=re.MULTILINE,
        )

        return line.rstrip()

class SlackEndpoint(Endpoint):
    def _invoke(self, r: Request, values: Mapping, settings: Mapping) -> Response:
        """
        Invokes the endpoint with the given request.
        """
        # Check if this is a retry and if we should ignore it
        retry_num = r.headers.get("X-Slack-Retry-Num")
        if not settings.get("allow_retry") and (
            r.headers.get("X-Slack-Retry-Reason") == "http_timeout"
            or ((retry_num is not None and int(retry_num) > 0))
        ):
            return Response(status=200, response="ok")

        # Parse the incoming JSON data
        data = r.get_json()

        # Handle Slack URL verification challenge
        if data.get("type") == "url_verification":
            return Response(
                response=json.dumps({"challenge": data.get("challenge")}),
                status=200,
                content_type="application/json",
            )

        # Handle Slack events
        if data.get("type") == "event_callback":
            event = data.get("event")

            # allowed_channel設定を取得
            allowed_channel_setting = settings.get("allowed_channel", "").strip()

            # Handle different event types
            if event.get("type") == "app_mention":
                # Handle mention events - when the bot is @mentioned
                message = event.get("text", "")

                # Remove the bot mention from the beginning of the message
                if message.startswith("<@"):
                    message = message.split("> ", 1)[1] if "> " in message else message

                # Get channel ID and thread timestamp
                channel = event.get("channel", "")
                # Use thread_ts if the message is in a thread, or use ts to start a new thread
                thread_ts = event.get("thread_ts", event.get("ts"))

                # Process the message and respond
                token = settings.get("bot_token")
                client = WebClient(token=token)

                # allowed_channel が指定されているかチェック
                if allowed_channel_setting:
                    try:
                        # チャンネルIDからチャンネル名を取得
                        channel_info = client.conversations_info(channel=channel)
                        actual_channel_name = channel_info["channel"]["name"]
                        # 取得したチャンネル名に "#" を付けて比較
                        current_channel_with_hash = f"#{actual_channel_name}"
                        if current_channel_with_hash != allowed_channel_setting:
                            # 許可されたチャンネルではなかった場合、メッセージを返して終了
                            client.chat_postMessage(
                                channel=channel,
                                thread_ts=thread_ts,
                                text=(
                                    f"Current channel: {current_channel_with_hash} is not allowed."
                                ),
                            )
                            return Response(status=200, response="ok", content_type="text/plain")
                    except SlackApiError as e:
                        print(f"Error getting channel info: {e}")
                        try:
                            client.chat_postMessage(
                                channel=channel,
                                thread_ts=thread_ts,
                                text=(
                                    f"Failed to retrieve channel info. SlackApiError: {str(e)}"
                                ),
                            )
                        except SlackApiError:
                            pass
                        return Response(status=200, response="ok", content_type="text/plain")
                    except Exception as e:
                        print(f"Unexpected error: {e}")
                        try:
                            client.chat_postMessage(
                                channel=channel,
                                thread_ts=thread_ts,
                                text=(
                                    f"An unexpected error occurred while retrieving channel info. Error: {str(e)}"
                                ),
                            )
                        except SlackApiError:
                            pass
                        return Response(status=200, response="ok", content_type="text/plain")

                try:
                    # Create a key to check if the conversation already exists
                    key_to_check = f"slack-{channel}-{thread_ts}"
                    conversation_id = None
                    try:
                        conversation_id = self.session.storage.get(key_to_check)
                    except Exception as e:
                        err = traceback.format_exc()

                    # Get thread history for better context
                    thread_history = []
                    if thread_ts:
                        # get thread history
                        try:
                            replies = client.conversations_replies(
                                channel=channel, ts=thread_ts
                            )
                            messages = replies.get("messages", [])

                            # user list in the thread
                            user_id_list = []
                            # pattern to extract user id from slack message
                            pattern = r'<@([^>]+)>'
                            # Format messages for context
                            for msg in messages:
                                role = "assistant" if msg.get("bot_id") else "user"
                                content = msg.get("text", "")
                                thread_history.append(
                                    {"role": role, "participant_id": msg.get("user", "unknown"), "content": content}
                                )
                                user_id = msg.get("user", "unknown")
                                if user_id != "unknown" and user_id not in user_id_list:
                                    user_id_list.append(user_id)
                                if content != "":
                                    user_ids = re.findall(pattern, content)
                                    for user_id in user_ids:
                                        if user_id not in user_id_list:
                                            user_id_list.append(user_id)
                        except SlackApiError as e:
                            print(f"Error getting thread history: {e}")

                        # get user display name map from user id list
                        user_display_name_map = {}
                        try:
                            for user_id in user_id_list:
                                user_info = client.users_info(user=user_id)
                                user_display_name = user_info.get("user", {}).get(
                                    "name", ""
                                )
                                user_real_name = user_info.get("user", {}).get(
                                    "real_name", ""
                                )
                                if user_display_name != "":
                                    user_display_name_map[user_id] = user_real_name + " (" + user_display_name + ")"
                                else:
                                    user_display_name_map[user_id] = user_real_name
                        except SlackApiError as e:
                            print(f"Error getting user info: {e}")

                        # add user display name to thread history
                        pattern = r"<@([A-Za-z0-9]+)>"
                        def replace_id_with_name(match):
                            user_id = match.group(1)  # <@...>の...部分を取り出す
                            # user_display_name_mapに存在する場合のみ置換
                            if user_id in user_display_name_map:
                                return f"@{user_display_name_map[user_id]}"
                            else:
                                # 不明なIDの場合はそのままにしておく
                                return match.group(0)
                        for msg in thread_history:
                            msg["participant_name"] = user_display_name_map.get(msg.get("participant_id", "unknown"), "unknown")
                            msg["content"] = re.sub(pattern, replace_id_with_name, msg["content"])

                    # Invoke the Dify app with the message
                    invoke_params = {
                        "app_id": settings["app"]["app_id"],
                        "query": re.sub(pattern, replace_id_with_name, message),
                        "inputs": {
                            "thread_history": json.dumps(
                                thread_history, indent=4, ensure_ascii=False
                            ),
                            "thread_users": json.dumps(
                                user_display_name_map, indent=4, ensure_ascii=False
                            ),
                            "thread_ts": thread_ts,
                        },
                        "response_mode": "blocking",
                    }
                    if conversation_id is not None:
                        invoke_params["conversation_id"] = conversation_id.decode(
                            "utf-8"
                        )

                    response = self.session.app.chat.invoke(**invoke_params)
                    answer = response.get("answer")
                    conversation_id = response.get("conversation_id")
                    if conversation_id:
                        self.session.storage.set(
                            key_to_check, conversation_id.encode("utf-8")
                        )

                    try:
                        converter = SlackMarkdownConverter()
                        converted_answer = converter.convert(answer)

                        # Slackで指定されている3,000文字以上の場合は分割
                        # https://api.slack.com/reference/block-kit/composition-objects#text__fields
                        MAX_MSG_LEN = 3000
                        chunks = [
                            converted_answer[i : i + MAX_MSG_LEN]
                            for i in range(0, len(converted_answer), MAX_MSG_LEN)
                        ]

                        # ブロードキャストするかどうか
                        reply_broadcast = settings.get("first_reply_broadcast", False) and len(thread_history) == 1

                        for i, chunk in enumerate(chunks):
                            # 分割したチャンクを blocks に載せる
                            answer_blocks = [
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": chunk
                                    }
                                }
                            ]
                            # 2つ目以降のメッセージでブロードキャストされるとスレッド外にも大量に通知されてしまうので、
                            # 必要に応じて一度目のみブロードキャストにする
                            chunk_reply_broadcast = reply_broadcast if i == 0 else False

                            client.chat_postMessage(
                                channel=channel,
                                text=chunk,  # fallback用テキスト
                                thread_ts=thread_ts,
                                blocks=answer_blocks,
                                reply_broadcast=chunk_reply_broadcast
                            )

                        return Response(status=200, response="ok", content_type="text/plain")

                    except SlackApiError as e:
                        return Response(
                            status=200,
                            response=f"Error sending message to Slack: {str(e)}",
                            content_type="text/plain",
                        )
                except Exception as e:
                    err = traceback.format_exc()

                    # Send error message to Slack
                    try:
                        client.chat_postMessage(
                            channel=channel,
                            thread_ts=thread_ts,
                            text=f"Sorry, I'm having trouble processing your request. Please try again later. Error: {str(e)}",
                        )
                    except SlackApiError:
                        # Failed to send error message
                        pass

                    return Response(
                        status=200,
                        response=f"An error occurred: {str(e)}\n{err}",
                        content_type="text/plain",
                    )
            else:
                # Other event types we're not handling
                return Response(status=200, response="ok")
        else:
            # Not an event we're handling
            return Response(status=200, response="ok")
