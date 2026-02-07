# Message Tool - Message Sending

Send messages via channel plugins (Discord, Telegram, WhatsApp, etc.).

## Usage

```bash
message action="send" target="#general" message="Hello!"
message action="broadcast" target="all-channels" message="Announcement"
message action="poll" target="#general" pollQuestion="最喜欢的语言?" pollOption='["Python","JavaScript","Go"]'
message action="react" target="message-id" emoji="👍"
message action="search" query="bug fix"
message action="channel-create" name="new-channel" topic="Discussion"
message action="channel-list"
```

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|------------|
| action | string | Yes | Action to perform |
| target | string | No | Target channel/user |
| message | string | No | Message content |
| channel | string | No | Channel type (discord, telegram, etc.) |
| quoteText | string | No | Quote for reply |
| replyTo | string | No | Message ID to reply to |
| pollQuestion | string | No | Poll question |
| pollOption | list | No | Poll options |
| pollDurationHours | int | No | Poll duration (default: 24) |
| pollMulti | bool | No | Allow multiple answers |
| emoji | string | No | Emoji reaction |
| query | string | No | Search query |
| name | string | No | Channel name |
| topic | string | No | Channel topic |
| nsfw | bool | No | Not safe for work |
| parentId | string | No | Parent channel ID |
| limit | int | No | Result limit |
| dryRun | bool | No | Simulate only |
| silent | bool | No | Send without notification |
| filePath | string | No | File to upload |
| contentType | string | No | MIME type |
| caption | string | No | Media caption |
| messageId | string | No | Message ID |

## Actions

### Messaging

| Action | Description |
|--------|------------|
| send | Send a message |
| broadcast | Broadcast to all channels |
| poll | Create a poll |
| react | Add reaction |
| reactions | List reactions |
| edit | Edit message |
| delete | Delete message |
| read | Read messages |

### Channels

| Action | Description |
|--------|------------|
| channel-list | List channels |
| channel-create | Create channel |
| channel-edit | Edit channel |
| channel-delete | Delete channel |
| channel-move | Move channel |
| category-create | Create category |
| category-edit | Edit category |
| category-delete | Delete category |

### Other

| Action | Description |
|--------|------------|
| search | Search messages |
| pin | Pin message |
| unpin | Unpin message |
| list-pins | List pinned messages |
| permissions | Manage permissions |
| thread-create | Create thread |
| thread-list | List threads |
| thread-reply | Reply to thread |
| member-info | Get member info |
| role-info | Get role info |
| emoji-list | List emojis |
| emoji-upload | Upload emoji |
| sticker-upload | Upload sticker |
| event-create | Create event |
| event-list | List events |
| voice-status | Get voice status |

## Examples

Send message to Discord:
```
message action="send" target="#general" message="Hello!" channel="discord"
```

Create poll:
```
message action="poll" target="#general" pollQuestion="颜色?" pollOption='["红色","蓝色"]' pollDurationHours=48
```

React to message:
```
message action="react" messageId="123456789" emoji="❤️"
```

Search messages:
```
message action="search" query="bug" limit=20
```

Create channel:
```
message action="channel-create" name="new-channel" topic="Discussion area"
```

Upload file:
```
message action="send" target="#general" filePath="/path/to/file.png" caption="Screenshot"
```
