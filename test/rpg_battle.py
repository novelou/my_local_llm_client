#!/usr/bin/env python3
"""
🐉 ダンジョン冒険記 - ターミナルRPGバトルゲーム 🗡️

プレイヤーがダンジョンを探索し、モンスターと戦ってボスを倒すゲーム。
レベルアップやアイテム使用で戦略的に戦おう！
"""

import random
import time
import sys

# ============================================================
# アスキーアート
# ============================================================
PLAYER_ART = """
    /\\_/\\  
   ( o.o ) 
    > ^ <  
   /|   |\\
  (_|   |_)\
"""

MONSTERS_ART = {
    "slime": """
      ___
     /   \\
    | ()()|
     \\___/
    ~~~~~~\
""",
    "skeleton": """
     ____
   .'    `.
  /  () () \\
 |    __    |
  \\  `--`  /
   `.____.'\
""",
    "dragon": """
        __
      .'.  `.
     /  \\__/  \\
    |   |||||  |
     \\  \\___/  /
      `.___.__'\
    /         \\
   '-----------'\
""",
}

# ============================================================
# ゲームデータ
# ============================================================
class Character:
    """キャラクターのベースクラス"""

    def __init__(self, name, hp, max_hp, mp, max_mp, atk, defense, level=1):
        self.name = name
        self.hp = hp
        self.max_hp = max_hp
        self.mp = mp
        self.max_mp = max_mp
        self.atk = atk
        self.defense = defense
        self.level = level
        self.exp = 0
        self.exp_to_next = 30

    @property
    def is_alive(self):
        return self.hp > 0

    def take_damage(self, damage):
        actual = max(1, damage - self.defense)
        self.hp = max(0, self.hp - actual)
        return actual

    def heal(self, amount):
        healed = min(amount, self.max_hp - self.hp)
        self.hp += healed
        return healed

    def gain_exp(self, exp_gain):
        self.exp += exp_gain
        leveled_up = False
        while self.exp >= self.exp_to_next:
            self.exp -= self.exp_to_next
            self.level_up()
            leveled_up = True
        return leveled_up

    def level_up(self):
        self.level += 1
        growth_rate = 0.2
        self.max_hp = int(self.max_hp * (1 + growth_rate))
        self.max_mp = int(self.max_mp * (1 + growth_rate))
        self.atk = int(self.atk * (1 + growth_rate))
        self.defense = int(self.defense * (1 + growth_rate))
        self.hp = self.max_hp  # レベルアップで全回復
        self.mp = self.max_mp
        self.exp_to_next = int(self.exp_to_next * 1.5)


class Player(Character):
    """プレイヤーキャラクター"""

    def __init__(self, name="勇者"):
        super().__init__(name=name, hp=100, max_hp=100, mp=30, max_mp=30,
                         atk=15, defense=5, level=1)
        self.gold = 0
        self.potions = 3
        self.eternal_water = 0

    def use_potion(self):
        if self.potions > 0:
            self.potions -= 1
            healed = self.heal(40)
            return True, f"ポーションを使った！HPが{healed}回復した！"
        return False, "ポーションがない！"

    def use_eternal_water(self):
        if self.eternal_water > 0:
            self.eternal_water -= 1
            healed = self.heal(80)
            restored_mp = min(20, self.max_mp - self.mp)
            self.mp += restored_mp
            return True, f"エターナルウォーターを使った！HPが{healed}、MPが{restored_mp}回復した！"
        return False, "エターナルウォーターがない！"

    def fireball(self):
        if self.mp >= 10:
            self.mp -= 10
            damage = int(self.atk * 2.5) + random.randint(5, 15)
            return True, f"🔥 ファイアボール！{damage}ダメージ！", damage
        return False, "MPが足りない！", 0

    def thunder_strike(self):
        if self.mp >= 15:
            self.mp -= 15
            damage = int(self.atk * 3) + random.randint(10, 25)
            return True, f"⚡ サンダーストライク！{damage}ダメージ！", damage
        return False, "MPが足りない！", 0

    def status(self):
        hp_bar = self._bar(self.hp, self.max_hp, 15)
        mp_bar = self._bar(self.mp, self.max_mp, 15)
        exp_bar = self._bar(self.exp, self.exp_to_next, 15)
        return (
            f"\n{'='*40}\n"
            f"  {PLAYER_ART}"
            f"  [{self.name}] Lv.{self.level}\n"
            f"  HP: {hp_bar} {self.hp}/{self.max_hp}\n"
            f"  MP: {mp_bar} {self.mp}/{self.max_mp}\n"
            f"  EXP:{exp_bar} {self.exp}/{self.exp_to_next}\n"
            f"  ATK:{self.atk} DEF:{self.defense} "
            f"金:{self.gold}💰 ポーション:{self.potions} 🧪 エタ水:{self.eternal_water}"
        )

    @staticmethod
    def _bar(current, maximum, length=15):
        filled = int(length * current / maximum) if maximum > 0 else 0
        return "█" * filled + "░" * (length - filled)


class Monster(Character):
    """モンスター"""

    TEMPLATES = {
        "slime": {"name": "スライム", "hp": 40, "mp": 0, "atk": 8, "defense": 2, "exp": 15, "gold": (5, 15)},
        "bat": {"name": "コウモリ", "hp": 30, "mp": 0, "atk": 12, "defense": 1, "exp": 12, "gold": (3, 10)},
        "skeleton": {"name": "スケルトン", "hp": 60, "mp": 5, "atk": 14, "defense": 6, "exp": 25, "gold": (10, 25)},
        "goblin": {"name": "ゴブリン", "hp": 50, "mp": 8, "atk": 16, "defense": 4, "exp": 20, "gold": (15, 30)},
        "dark_knight": {"name": "ダークナイト", "hp": 100, "mp": 20, "atk": 22, "defense": 10, "exp": 50, "gold": (40, 60)},
        "dragon": {"name": "🐉 ドラゴンボス", "hp": 200, "mp": 50, "atk": 35, "defense": 15, "exp": 200, "gold": (100, 200)},
    }

    def __init__(self, kind="slime"):
        tmpl = self.TEMPLATES[kind]
        super().__init__(name=tmpl["name"], hp=tmpl["hp"], max_hp=tmpl["hp"],
                         mp=tmpl["mp"], max_mp=tmpl["mp"],
                         atk=tmpl["atk"], defense=tmpl["defense"])
        self.kind = kind
        self.exp_reward = tmpl["exp"]
        self.gold_range = tmpl["gold"]

    def special_attack(self):
        """特殊攻撃（MPがあれば使用）"""
        if self.mp > 0 and random.random() < 0.4:
            self.mp -= 5
            damage = int(self.atk * 1.8) + random.randint(3, 10)
            return True, f"🔥 {self.name}の特殊攻撃！{damage}ダメージ！", damage
        return False, None, 0

    def art(self):
        if self.kind in MONSTERS_ART:
            return MONSTERS_ART[self.kind]
        return "  👾\n   |"


# ============================================================
# ゲームエンジン
# ============================================================
class DungeonGame:
    """ダンジョン冒険記のゲームエンジン"""

    FLOOR_MONSTERS = {
        1: ["slime", "bat"],
        2: ["skeleton", "goblin"],
        3: ["dark_knight", "skeleton"],
        4: ["dragon"],  # ボスフロア
    }

    def __init__(self):
        self.player = Player()
        self.current_floor = 1
        self.max_floors = 4
        self.battles_won = 0
        self.shop_items = {
            "potion": {"name": "ポーション", "price": 20, "desc": "HPを40回復"},
            "eternal_water": {"name": "エターナルウォーター", "price": 50, "desc": "HP80・MP20回復"},
        }

    def print_slow(self, text, delay=0.03):
        """テキストをゆっくり表示"""
        for char in text:
            sys.stdout.write(char)
            sys.stdout.flush()
            time.sleep(delay)
        print()

    def clear_screen(self):
        """画面クリア（視覚的な区切り）"""
        print("\n" + "─" * 50 + "\n")

    def encounter_monster(self):
        """モンスターを生成"""
        floor = self.current_floor
        if floor not in self.FLOOR_MONSTERS:
            floor = max(self.FLOOR_MONSTERS.keys())
        kinds = self.FLOOR_MONSTERS[floor]
        kind = random.choice(kinds)

        # フロアに応じてモンスターを強化
        monster = Monster(kind)
        scale = 1 + (floor - 1) * 0.3
        monster.max_hp = int(monster.hp * scale)
        monster.hp = monster.max_hp
        monster.atk = int(monster.atk * scale)

        return monster

    def battle(self, monster):
        """戦闘を実行"""
        print(f"\n{'#'*50}")
        self.print_slow(f"  ⚔️ {monster.name} が現れた！")
        print(f"{'#'*50}\n")
        print(monster.art())

        while self.player.is_alive and monster.is_alive:
            # プレイヤーターン
            print(self.player.status())
            print("\n  行動を選択:")
            print("  [1] 普通攻撃 ⚔️")
            print("  [2] ファイアボール 🔥 (MP10)")
            print("  [3] サンダーストライク ⚡ (MP15)")
            print("  [4] ポーション 🧪")
            print("  [5] エターナルウォーター 💧")

            choice = input("\n  > ").strip()

            player_action_msg = ""
            damage_to_monster = 0

            if choice == "1":
                # 普通攻撃
                crit = random.random() < 0.2  # 20%クリティカル
                base_dmg = self.player.atk + random.randint(-3, 5)
                if crit:
                    damage_to_monster = int(base_dmg * 1.8)
                    player_action_msg = f"💥 クリティカル！{damage_to_monster}ダメージ！"
                else:
                    damage_to_monster = max(1, base_dmg - monster.defense)
                    player_action_msg = f"{damage_to_monster}ダメージを与えた！"

            elif choice == "2":
                success, msg, dmg = self.player.fireball()
                if success:
                    damage_to_monster = dmg
                    player_action_msg = msg
                else:
                    print(f"\n  {msg}")
                    continue

            elif choice == "3":
                success, msg, dmg = self.player.thunder_strike()
                if success:
                    damage_to_monster = dmg
                    player_action_msg = msg
                else:
                    print(f"\n  {msg}")
                    continue

            elif choice == "4":
                success, msg = self.player.use_potion()
                print(f"\n  {msg}")
                player_action_msg = ""

            elif choice == "5":
                success, msg = self.player.use_eternal_water()
                print(f"\n  {msg}")
                player_action_msg = ""

            else:
                print("\n  無効な選択...")
                continue

            # モンスターにダメージを適用
            if damage_to_monster > 0:
                monster.hp = max(0, monster.hp - damage_to_monster)
                print(f"  {player_action_msg}")
                time.sleep(0.5)

            # モンスターが倒れたかチェック
            if not monster.is_alive:
                self.on_victory(monster)
                return True

            # モンスターターン
            print("\n  ── モンスターのターン ──")
            special_success, special_msg, special_dmg = monster.special_attack()

            if special_success:
                actual = self.player.take_damage(special_dmg)
                print(f"  {special_msg}")
                time.sleep(0.5)
            else:
                base_dmg = monster.atk + random.randint(-2, 4)
                actual = self.player.take_damage(base_dmg)
                print(f"  {monster.name}の攻撃！{actual}ダメージを受けた！")
                time.sleep(0.5)

            # プレイヤーが倒れたかチェック
            if not self.player.is_alive:
                break

        return False

    def on_victory(self, monster):
        """勝利処理"""
        gold_gained = random.randint(*monster.gold_range)
        exp_gained = monster.exp_reward
        self.battles_won += 1

        print(f"\n{'#'*50}")
        self.print_slow(f"  🎉 {monster.name} を倒した！")
        print(f"  💰 金貨: +{gold_gained}")
        print(f"  ⭐ EXP:   +{exp_gained}")

        self.player.gold += gold_gained
        leveled = self.player.gain_exp(exp_gained)

        if leveled:
            self.print_slow(f"  🎊 レベルアップ！Lv.{self.player.level} に到達！")

        # ランダムドロップ
        if random.random() < 0.3:
            self.player.potions += 1
            print("  🧪 ポーションを入手した！")
        elif random.random() < 0.15:
            self.player.eternal_water += 1
            print("  💧 エターナルウォーターを入手した！")

        # フロア進行判定（2戦ごとにフロアアップ）
        if self.battles_won % 2 == 0 and self.current_floor < self.max_floors:
            self.current_floor += 1
            print(f"\n  🏰 {self.current_floor}F に進んだ！")

    def visit_shop(self):
        """ショップ訪問"""
        print(f"\n{'='*40}")
        print("  🛒 ショップにようこそ！")
        print(f"  あなたの金貨: {self.player.gold}💰\n")

        for key, item in self.shop_items.items():
            print(f"  [{key}] {item['name']} - {item['price']}💰 ({item['desc']})")

        print("\n  [q] 退出")
        choice = input("\n  購入するアイテム: ").strip().lower()

        if choice in self.shop_items:
            item = self.shop_items[choice]
            if self.player.gold >= item["price"]:
                self.player.gold -= item["price"]
                if choice == "potion":
                    self.player.potions += 1
                elif choice == "eternal_water":
                    self.player.eternal_water += 1
                print(f"\n  ✅ {item['name']} を購入した！")
            else:
                print("\n  💸 金貨が足りない！")

    def rest_at_inn(self):
        """宿で休憩"""
        inn_cost = 10 * self.current_floor
        if self.player.gold >= inn_cost:
            self.player.gold -= inn_cost
            old_hp = self.player.hp
            old_mp = self.player.mp
            self.player.hp = self.player.max_hp
            self.player.mp = self.player.max_mp
            healed_hp = self.player.hp - old_hp
            healed_mp = self.player.mp - old_mp
            print(f"\n  🛏️ {inn_cost}💰を支払って休憩した！")
            print(f"  HPが{healed_hp}、MPが{healed_mp}回復した！")
        else:
            print(f"\n  💸 宿代({inn_cost}💰)が足りない...")

    def game_over(self):
        """ゲームオーバー"""
        print("\n" + "█" * 50)
        self.print_slow("  ☠️ ゲームオーバー ☠️")
        print(f"  {self.player.name} は倒れてしまった...\n")
        print(f"  ── クリアレポート ──")
        print(f"  到達フロア:   {self.current_floor}F")
        print(f"  勝利数:       {self.battles_won}")
        print(f"  最終レベル:   Lv.{self.player.level}")
        print(f"  所持金貨:     {self.player.gold}💰")
        print("█" * 50)

    def victory_screen(self):
        """クリア画面"""
        print("\n" + "★" * 50)
        self.print_slow("  🏆 ダンジョンを攻略した！🏆")
        print(f"\n  {self.player.name} はドラゴンボスを倒し、")
        print(f"  伝説の勇者となった！！！\n")
        print(f"  ── クリアレポート ──")
        print(f"  最終レベル:   Lv.{self.player.level}")
        print(f"  勝利数:       {self.battles_won}")
        print(f"  所持金貨:     {self.player.gold}💰")
        print("★" * 50)

    def run(self):
        """ゲームメインループ"""
        # タイトル画面
        title = """
╔══════════════════════════════════════╗
║                                      ║
║       🐉 ダンジョン冒険記 🗡️           ║
║                                      ║
║    4階のドラゴンボスを倒せ！         ║
║                                      ║
╚══════════════════════════════════════╝
"""
        print(title)
        self.print_slow("  ダンジョンに挑む勇者の名前を入力:")
        name = input("  > ").strip() or "勇者"
        self.player.name = name

        self.clear_screen()
        self.print_slow(f"  {name} の冒険が始まる！")
        self.print_slow("  4階のドラゴンボスを倒してダンジョンを攻略しよう！\n")

        # メインゲームループ
        while self.player.is_alive:
            print(self.player.status())
            print(f"\n  📍 現在地: {self.current_floor}F")
            print("\n  行動を選択:")
            print("  [1] 探索してモンスターと戦う ⚔️")
            print("  [2] ショップでアイテムを購入 🛒")
            print("  [3] 宿で休憩する 🛏️")
            print("  [q] ゲームを終了")

            choice = input("\n  > ").strip().lower()

            if choice == "1":
                # 戦闘
                monster = self.encounter_monster()
                won = self.battle(monster)

                if not self.player.is_alive:
                    break

                # ボス撃破チェック
                if monster.kind == "dragon" and won:
                    self.victory_screen()
                    return

            elif choice == "2":
                self.visit_shop()

            elif choice == "3":
                self.rest_at_inn()

            elif choice == "q":
                print("\n  👋 ゲームを終了しました。お疲れ様でした！")
                return

            else:
                print("\n  無効な選択です...")

        # ゲームオーバー
        if not self.player.is_alive:
            self.game_over()


# ============================================================
# エントリポイント
# ============================================================
if __name__ == "__main__":
    try:
        game = DungeonGame()
        game.run()
    except KeyboardInterrupt:
        print("\n\n  👋 ゲームを中断しました。")
        sys.exit(0)
