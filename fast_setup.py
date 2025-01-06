import os
import json

if __name__ == "__main__":
    print("-- Fast setup session started. --")

    api_key = input("YOUR API KEY: ")
    api_secret = input("YOUR API SECRET: ")
    demo_trade = True if input("Enable demo trading?(Y/N): ").lower() == 'y' else False
    
    configs_dir = os.path.join(os.path.curdir, "configs.template")

    if os.path.exists(configs_dir):
        config_files = os.listdir(configs_dir)

        for file in config_files:
            old_file = os.path.join(configs_dir, file)
            new_file = os.path.join(configs_dir, file.replace('.template', ''))
            if not os.path.isfile(old_file):
                continue
            os.rename(old_file, new_file)

        new_configs_dir = configs_dir.replace('.template', '', 1)
        os.rename(configs_dir, new_configs_dir)

    else:   # os.path.exists(configs_dir)
        raise OSError("`configs.template` does not exist.")

    with open("./configs/exchange_config.json", "r+", encoding="utf-8") as F:
        config = json.load(F)
        config["api"]["bybit"]["apiKey"] = api_key
        config["api"]["bybit"]["secret"] = api_secret
        if demo_trade:
            config["demo_trade"] = True
        F.seek(0)
        json.dump(config, F, indent=4)
        F.truncate()

    print("-- Fast setup completed. --")