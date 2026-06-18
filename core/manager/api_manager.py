import pendulum

from astrbot.api.web import error_response, json_response

from .file_manager import file_manager


class SmartFilterAPIManager:
    """SmartFilter API manager for handling web API requests.

    This is a singleton class that provides methods for managing violations,
    bans, and unban operations through the web interface.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._plugin = None
        return cls._instance

    def initialize(self, plugin):
        """Initialize the API manager with a reference to the plugin instance.
        Args:
            plugin: The SmartFilter plugin instance.
        """
        self._plugin = plugin

    async def get_violations(self):
        """Get all violations grouped by platform and user.
        Returns:
            JSON response with violations list.
        """
        async with self._plugin._sf_lock:
            violations = []
            for platform, users in self._plugin.ban_list["prohibits"].items():
                for user_id, messages in users.items():
                    for msg in messages:
                        violations.append(
                            {
                                "platform": platform,
                                "user_id": user_id,
                                "message": msg["word"],
                                "time": msg["time"],
                                "is_banned": user_id
                                in self._plugin.ban_list["banners"].get(platform, {}),
                            }
                        )
            return json_response(violations)

    async def ban_users(self, users: list, duration: dict):
        """Ban selected users for a specified duration.
        Args:
            users: List of users to ban, each with platform and user_id.
            duration: Duration dict with years, months, days, hours.
        Returns:
            JSON response with ban results.
        """
        if not users:
            return error_response("No users selected", status_code=400)

        try:
            years = duration.get("years", 0)
            months = duration.get("months", 0)
            days = duration.get("days", 0)
            hours = duration.get("hours", 0)

            if years == 0 and months == 0 and days == 0 and hours == 0:
                return error_response("Invalid duration", status_code=400)

            ban_duration = pendulum.duration(
                years=years, months=months, days=days, hours=hours
            )
        except Exception:
            return error_response("Invalid duration format", status_code=400)

        results = []
        async with self._plugin._sf_lock:
            for user in users:
                platform = user.get("platform")
                user_id = user.get("user_id")

                if not platform or not user_id:
                    results.append(
                        {
                            "platform": platform,
                            "user_id": user_id,
                            "status": "error",
                            "message": "Missing platform or user_id",
                        }
                    )
                    continue

                if platform not in self._plugin.ban_list["available_platforms"]:
                    results.append(
                        {
                            "platform": platform,
                            "user_id": user_id,
                            "status": "error",
                            "message": "Invalid platform",
                        }
                    )
                    continue

                status, detail = await self._plugin.ban_user(
                    user_id, platform, ban_duration
                )
                results.append(
                    {
                        "platform": platform,
                        "user_id": user_id,
                        "status": "success" if status == "Success" else "error",
                        "message": detail,
                    }
                )

            await file_manager.write_file(self._plugin.ban_list)

        return json_response({"results": results})

    async def clear_violations(self, users: list):
        """Clear violations for specified users.
        Args:
            users: List of users to clear, each with platform and user_id.
        Returns:
            JSON response with clear results.
        """
        if not users:
            return error_response("No users selected", status_code=400)

        results = []
        async with self._plugin._sf_lock:
            for user in users:
                platform = user.get("platform")
                user_id = user.get("user_id")

                if not platform or not user_id:
                    results.append(
                        {
                            "platform": platform,
                            "user_id": user_id,
                            "status": "error",
                            "message": "Missing platform or user_id",
                        }
                    )
                    continue

                if platform not in self._plugin.ban_list["available_platforms"]:
                    results.append(
                        {
                            "platform": platform,
                            "user_id": user_id,
                            "status": "error",
                            "message": "Invalid platform",
                        }
                    )
                    continue

                if user_id not in self._plugin.ban_list["prohibits"][platform]:
                    results.append(
                        {
                            "platform": platform,
                            "user_id": user_id,
                            "status": "error",
                            "message": "No violations found",
                        }
                    )
                    continue

                self._plugin.ban_list["prohibits"][platform].pop(user_id)
                results.append(
                    {
                        "platform": platform,
                        "user_id": user_id,
                        "status": "success",
                        "message": "Violations cleared",
                    }
                )

            await file_manager.write_file(self._plugin.ban_list)

        return json_response({"results": results})

    async def unban_users(self, users: list):
        """Unban specified users.
        Args:
            users: List of users to unban, each with platform and user_id.
        Returns:
            JSON response with unban results.
        """
        if not users:
            return error_response("No users selected", status_code=400)

        results = []
        async with self._plugin._sf_lock:
            for user in users:
                platform = user.get("platform")
                user_id = user.get("user_id")

                if not platform or not user_id:
                    results.append(
                        {
                            "platform": platform,
                            "user_id": user_id,
                            "status": "error",
                            "message": "Missing platform or user_id",
                        }
                    )
                    continue

                if platform not in self._plugin.ban_list["available_platforms"]:
                    results.append(
                        {
                            "platform": platform,
                            "user_id": user_id,
                            "status": "error",
                            "message": "Invalid platform",
                        }
                    )
                    continue

                if user_id not in self._plugin.ban_list["banners"][platform]:
                    results.append(
                        {
                            "platform": platform,
                            "user_id": user_id,
                            "status": "error",
                            "message": "User not banned",
                        }
                    )
                    continue

                self._plugin.ban_list["banners"][platform].pop(user_id)
                results.append(
                    {
                        "platform": platform,
                        "user_id": user_id,
                        "status": "success",
                        "message": "User unbanned",
                    }
                )

            await file_manager.write_file(self._plugin.ban_list)

        return json_response({"results": results})


api_manager = SmartFilterAPIManager()
