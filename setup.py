import os

if __name__ == "__main__":
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
        print('`configs.template` does not exist.')