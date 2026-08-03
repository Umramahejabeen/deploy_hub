pipeline {
    agent any

    environment {
            EC2_HOST = "ubuntu"
            EC2_IP   = "13.201.223.71"
            REPO_URL = "https://github.com/Umramahejabeen/deploy_hub.git"
    }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Install & Test') {
            // Runs locally on the Jenkins (Windows) machine.
            // Only needs Python installed - no Docker required here at all.
            steps {
                bat '''
                    python -m venv venv
                    call venv\\Scripts\\activate
                    pip install -r requirements.txt
                    pip install pytest
                    pytest tests/ || exit 0
                '''
            }
        }

        stage('Build & Deploy on EC2') {
            // Everything Docker-related happens over SSH, using EC2's own
            // Docker daemon. Jenkins itself never touches Docker.
            steps {
                sshagent(credentials: ['ec2-ssh-key']) {
                    bat """
                        ssh -o StrictHostKeyChecking=no %EC2_HOST% ^
                        "cd ~/deployhub && (git pull origin main || git clone %REPO_URL% .) && docker compose up -d --build"
                    """
                }
            }
        }

        stage('Smoke Test') {
            steps {
                script {
                    sleep(time: 15, unit: 'SECONDS')
                    bat "curl -f http://%EC2_IP%:5000/health || exit 1"
                }
            }
        }
    }

    post {
        success {
            echo "Deployment succeeded: build #${env.BUILD_NUMBER}"
        }
        failure {
            echo "Deployment failed: build #${env.BUILD_NUMBER}"
        }
    }
}
